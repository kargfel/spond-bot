import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.spond_client import SpondAuthError
from app.workers import executioner
from app.workers.executioner import _Prepared

OPEN = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)


def prepared(accepted=True, invite=OPEN):
    http = MagicMock()
    http.close = AsyncMock()
    return _Prepared(http=http, token="tok", recipient_id="M-1", accepted=accepted,
                     invite_time=invite, created=time.monotonic())


def row(choice="accept", invite=OPEN):
    return MagicMock(user_choice=choice, invite_time=invite)


@pytest.fixture(autouse=True)
def clean():
    executioner._PREPARED.clear()
    yield
    executioner._PREPARED.clear()


def test_matches_only_while_decision_and_opening_time_are_unchanged():
    p = prepared(accepted=True)
    assert p.matches(row("accept"))
    assert p.matches(row("accept", invite=OPEN.replace(tzinfo=None)))  # naive == UTC
    assert not p.matches(row("decline"))
    assert not p.matches(row("manual"))
    assert not p.matches(row("accept", invite=OPEN + timedelta(minutes=5)))


@pytest.mark.asyncio
async def test_prepared_connection_sends_without_login_or_lookups():
    p = prepared()
    user = MagicMock(display_name="Mara", profile_id="P")
    timings = {"retries": 0}
    with patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock) as tok, \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock) as bulk, \
         patch.object(executioner.spond_client, "rsvp", new_callable=AsyncMock) as rsvp:
        await executioner._submit_rsvp(AsyncMock(), user, "SP-1", True, timings=timings, prepared=p)
    tok.assert_not_called()
    bulk.assert_not_called()
    assert rsvp.call_args.args == (p.http, "tok", "SP-1", "M-1", True)
    assert timings["prepared"] is True and timings["attempts"] == 1


@pytest.mark.asyncio
async def test_prepared_is_ignored_when_forcing_a_relogin():
    p = prepared()
    user = MagicMock(display_name="Mara", profile_id="P")
    with patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="new") as tok, \
         patch.object(executioner.spond_client, "rsvp", new_callable=AsyncMock) as rsvp:
        await executioner._submit_rsvp(AsyncMock(), user, "SP-1", True, force_refresh=True,
                                       resolved_recipient_id="M-1", prepared=p)
    assert tok.call_args.kwargs["force"] is True
    assert rsvp.call_args.args[1] == "new" and rsvp.call_args.args[0] is not p.http


@pytest.mark.asyncio
async def test_only_the_first_attempt_uses_the_prepared_connection():
    p = prepared()
    ev = MagicMock(spond_event_id="SP-1", resolved_recipient_id=None)
    ok = datetime.now(timezone.utc)
    sends = AsyncMock(side_effect=[SpondAuthError("expired"), ok])
    with patch.object(executioner, "_submit_rsvp", sends):
        await executioner._submit_with_retries(AsyncMock(), MagicMock(), ev, True, {"retries": 0}, p)
    assert sends.await_args_list[0].kwargs["prepared"] is p
    assert sends.await_args_list[1].kwargs["prepared"] is None


@pytest.mark.asyncio
async def test_sniper_hands_matching_prepared_connection_to_the_process():
    event_id = uuid.uuid4()
    p = prepared()
    executioner._PREPARED[event_id] = p
    db = AsyncMock()
    db.get = AsyncMock(return_value=row("accept"))
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    with patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock) as proc:
        await executioner.run_sniper(event_id)
    assert proc.call_args.args[1] is p
    assert event_id not in executioner._PREPARED


@pytest.mark.asyncio
async def test_sniper_drops_stale_prepared_connection_and_still_sends():
    event_id = uuid.uuid4()
    p = prepared(accepted=True)
    executioner._PREPARED[event_id] = p
    db = AsyncMock()
    db.get = AsyncMock(return_value=row("decline"))  # member changed their mind after warmup
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    with patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock) as proc:
        await executioner.run_sniper(event_id)
    assert proc.call_args.args[1] is None
    p.http.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_event_always_closes_the_prepared_connection():
    p = prepared()
    with patch.object(executioner, "_process_event_inner", new_callable=AsyncMock, side_effect=RuntimeError("x")):
        with pytest.raises(RuntimeError):
            await executioner._process_event(MagicMock(), p)
    p.http.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_closes_only_old_prepared_connections():
    old, fresh = prepared(), prepared()
    old.created -= 500
    a, b = uuid.uuid4(), uuid.uuid4()
    executioner._PREPARED.update({a: old, b: fresh})
    await executioner.sweep_prepared()
    assert list(executioner._PREPARED) == [b]
    old.http.close.assert_awaited_once()
    fresh.http.close.assert_not_called()


@pytest.mark.asyncio
async def test_open_prepared_stores_a_checked_connection():
    ev = MagicMock(id=uuid.uuid4(), user_choice="decline", invite_time=OPEN)
    with patch.object(executioner.spond_client, "get_profile_id", new_callable=AsyncMock, return_value="P") as prof:
        await executioner._open_prepared(AsyncMock(), MagicMock(), ev, "tok", "M-1")
    stored = executioner._PREPARED[ev.id]
    assert (stored.token, stored.recipient_id, stored.accepted) == ("tok", "M-1", False)
    prof.assert_awaited_once()
    await stored.close()


@pytest.mark.asyncio
async def test_open_prepared_relogs_in_on_a_rejected_token():
    ev = MagicMock(id=uuid.uuid4(), user_choice="accept", invite_time=OPEN)
    prof = AsyncMock(side_effect=[SpondAuthError("old"), "P"])
    with patch.object(executioner.spond_client, "get_profile_id", prof), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="fresh"):
        await executioner._open_prepared(AsyncMock(), MagicMock(), ev, "stale", "M-1")
    assert executioner._PREPARED[ev.id].token == "fresh"
    await executioner._discard_prepared(ev.id)


@pytest.mark.asyncio
async def test_open_prepared_never_raises_and_stores_nothing_on_failure():
    ev = MagicMock(id=uuid.uuid4(), user_choice="accept", invite_time=OPEN)
    with patch.object(executioner.spond_client, "get_profile_id", new_callable=AsyncMock,
                      side_effect=OSError("no route")):
        await executioner._open_prepared(AsyncMock(), MagicMock(), ev, "tok", "M-1")
    assert ev.id not in executioner._PREPARED
