import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.spond_client import SpondAPIError, SpondAuthError
from app.workers import executioner
from app.workers.executioner import _Prepared

OPEN = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)


def prepared(accepted=True, invite=OPEN):
    http = MagicMock()
    http.close = AsyncMock()
    return _Prepared(http=http, token="tok", spond_event_id="SP-1", recipient_id="M-1",
                     accepted=accepted, invite_time=invite, created=time.monotonic())


def row(choice="accept", invite=OPEN, status="pending"):
    return MagicMock(user_choice=choice, invite_time=invite, status=status)


@pytest.fixture(autouse=True)
def clean():
    executioner._PREPARED.clear()
    executioner._INFLIGHT.clear()
    yield
    executioner._PREPARED.clear()
    executioner._INFLIGHT.clear()


def test_matches_only_while_decision_and_opening_time_are_unchanged():
    p = prepared(accepted=True)
    assert p.matches(row("accept"))
    assert p.matches(row("accept", invite=OPEN.replace(tzinfo=None)))  # naive == UTC
    assert not p.matches(row("decline"))
    assert not p.matches(row("manual"))
    assert not p.matches(row("accept", invite=OPEN + timedelta(minutes=5)))


# --- the fast send ---------------------------------------------------------

@pytest.mark.asyncio
async def test_prepared_answer_is_sent_before_any_database_access():
    event_id = uuid.uuid4()
    p = prepared()
    executioner._PREPARED[event_id] = p
    order = []
    rsvp = AsyncMock(side_effect=lambda *a: order.append("put"))

    async def process(stub, **kw):
        order.append("db")
        process.kw = kw

    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "AsyncSessionLocal", side_effect=AssertionError("db read before send")), \
         patch.object(executioner, "_process_event", side_effect=process):
        await executioner.run_sniper(event_id)

    assert order == ["put", "db"]
    assert rsvp.call_args.args == (p.http, "tok", "SP-1", "M-1", True)
    presend = process.kw["presend"]
    assert presend.timings["prepared"] is True and presend.timings["attempts"] == 1
    assert presend.timings["request_ms"] >= 0
    p.http.close.assert_awaited()
    assert event_id not in executioner._INFLIGHT and event_id not in executioner._PREPARED


@pytest.mark.asyncio
async def test_failed_prepared_send_continues_on_the_normal_path_with_a_fresh_login_after_401():
    event_id = uuid.uuid4()
    executioner._PREPARED[event_id] = prepared()
    process = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", AsyncMock(side_effect=SpondAuthError("expired"))), \
         patch.object(executioner, "_process_event", process):
        await executioner.run_sniper(event_id)
    kw = process.await_args.kwargs
    assert kw["force_first"] is True and kw["carry"]["retries"] == 1 and kw["carry"]["recipient_id"] == "M-1"
    assert "presend" not in kw


@pytest.mark.asyncio
async def test_other_prepared_failure_keeps_retrying_without_forcing_a_login():
    event_id = uuid.uuid4()
    executioner._PREPARED[event_id] = prepared()
    process = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", AsyncMock(side_effect=SpondAPIError("busy", 503))), \
         patch.object(executioner, "_process_event", process):
        await executioner.run_sniper(event_id)
    assert process.await_args.kwargs["force_first"] is False


@pytest.mark.asyncio
async def test_without_prepared_data_the_normal_path_runs_for_a_pending_decision():
    event_id = uuid.uuid4()
    db = AsyncMock()
    ev = row("accept")
    ev.status = "pending"
    db.get = AsyncMock(return_value=ev)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    with patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock) as proc:
        await executioner.run_sniper(event_id)
    proc.assert_awaited_once_with(ev)


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,status", [("manual", "pending"), ("accept", "processed"), ("accept", "failed")])
async def test_normal_path_skips_events_that_changed_during_the_head_start(choice, status):
    db = AsyncMock()
    db.get = AsyncMock(return_value=row(choice, status=status))
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    with patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock) as proc:
        await executioner.run_sniper(uuid.uuid4())
    proc.assert_not_called()


@pytest.mark.asyncio
async def test_sniper_waits_for_the_exact_instant_then_takes_data_prepared_meanwhile():
    event_id = uuid.uuid4()
    fire_at = datetime.now(timezone.utc) + timedelta(milliseconds=120)
    sent_at = []

    async def rsvp(*a):
        sent_at.append(datetime.now(timezone.utc))

    task = asyncio.create_task(executioner.run_sniper(event_id, fire_at))
    await asyncio.sleep(0.02)
    assert event_id in executioner._INFLIGHT          # visible to the executioner while waiting
    executioner._PREPARED[event_id] = prepared()      # warmup finished late: still used
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock):
        await task
    assert sent_at and sent_at[0] >= fire_at - timedelta(milliseconds=2)
    assert (sent_at[0] - fire_at) < timedelta(milliseconds=50)


@pytest.mark.asyncio
async def test_cancelling_during_the_head_start_stops_the_answer():
    event_id = uuid.uuid4()
    executioner._PREPARED[event_id] = prepared()
    fire_at = datetime.now(timezone.utc) + timedelta(milliseconds=80)
    task = asyncio.create_task(executioner.run_sniper(event_id, fire_at))
    await asyncio.sleep(0.02)
    executioner.cancel_sniper(MagicMock(), event_id)   # member switched to "Leave to me"
    await asyncio.sleep(0)
    db = AsyncMock()
    db.get = AsyncMock(return_value=row("manual"))
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock) as proc:
        await task
    rsvp.assert_not_called()
    proc.assert_not_called()


# --- executioner leaves in-flight events alone ---------------------------------

@pytest.mark.asyncio
async def test_executioner_skips_events_a_sniper_is_working_on():
    busy, free = MagicMock(id=uuid.uuid4()), MagicMock(id=uuid.uuid4())
    executioner._INFLIGHT.add(busy.id)
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [busy, free]
    db.execute = AsyncMock(return_value=result)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    with patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_process_event", new_callable=AsyncMock) as proc:
        await executioner.run_executioner()
    assert [c.args[0] for c in proc.await_args_list] == [free]


# --- recording an answer that already went out -------------------------------

@pytest.mark.asyncio
async def test_presend_is_recorded_without_sending_again(monkeypatch):
    mock_result = MagicMock(rowcount=1)
    ev = MagicMock(id=uuid.uuid4(), user_id=uuid.uuid4(), user_choice="accept", heading="H",
                   status="processing", invite_time=OPEN, spond_event_id="SP-1")
    user = MagicMock(display_name="Mara", profile_id="P")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.get = AsyncMock(side_effect=[ev, user])
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    sent = datetime.now(timezone.utc)
    presend = executioner._PreSend(sent - timedelta(milliseconds=3), sent,
                                   {"retries": 0, "attempts": 1, "prepared": True})
    with patch.object(executioner, "AsyncSessionLocal", return_value=db), \
         patch.object(executioner, "_submit_with_retries", new_callable=AsyncMock) as submit, \
         patch.object(executioner, "_write_rsvp_log", new_callable=AsyncMock) as log, \
         patch.object(executioner, "_notify_member", new_callable=AsyncMock), \
         patch.object(executioner.bus, "publish_admin", new_callable=AsyncMock):
        await executioner._process_event(MagicMock(id=ev.id), presend=presend)
    submit.assert_not_called()
    assert ev.status == "processed"
    assert log.await_args.args[3:6] == (presend.fired_at, sent, "success")


@pytest.mark.asyncio
async def test_presend_that_cannot_claim_the_event_is_not_recorded_twice():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    presend = executioner._PreSend(OPEN, OPEN, {"retries": 0})
    with patch.object(executioner, "AsyncSessionLocal", return_value=db):
        await executioner._process_event(MagicMock(id=uuid.uuid4()), presend=presend)
    db.get.assert_not_called()


# --- preparing ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_changed_decision_drops_prepared_data():
    ev = MagicMock(id=uuid.uuid4(), user_choice="decline", invite_time=OPEN + timedelta(days=1))
    p = prepared(accepted=True)
    executioner._PREPARED[ev.id] = p
    sched = MagicMock()
    executioner.schedule_sniper(sched, ev)
    await asyncio.sleep(0)
    assert ev.id not in executioner._PREPARED
    p.http.close.assert_awaited()


@pytest.mark.asyncio
async def test_unchanged_decision_keeps_prepared_data():
    inv = datetime.now(timezone.utc) + timedelta(days=1)
    ev = MagicMock(id=uuid.uuid4(), user_choice="accept", invite_time=inv)
    executioner._PREPARED[ev.id] = prepared(accepted=True, invite=inv)
    executioner.schedule_sniper(MagicMock(), ev)
    await asyncio.sleep(0)
    assert ev.id in executioner._PREPARED


@pytest.mark.asyncio
async def test_sniper_job_starts_early_and_carries_the_exact_instant():
    inv = datetime.now(timezone.utc) + timedelta(hours=2)
    ev = MagicMock(id=uuid.uuid4(), user_choice="accept", invite_time=inv)
    sched = MagicMock()
    with patch("app.config.settings.rsvp_lead_time_ms", 0):
        executioner.schedule_sniper(sched, ev)
    job = next(c for c in sched.add_job.call_args_list if c.args[0] is executioner.run_sniper)
    assert job.kwargs["run_date"] == inv - timedelta(seconds=executioner._SNIPER_HEADSTART_S)
    assert job.kwargs["args"] == [ev.id, inv]


def test_sweep_and_open_prepared_helpers_exist():
    assert callable(executioner.sweep_prepared) and callable(executioner._open_prepared)


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
async def test_open_prepared_stores_a_checked_connection(caplog):
    ev = MagicMock(id=uuid.uuid4(), user_choice="decline", invite_time=OPEN, heading="League match",
                   spond_event_id="SP-9")
    user = MagicMock(display_name="Mara Lind")
    with caplog.at_level("INFO", logger=executioner.logger.name), \
         patch.object(executioner.spond_client, "get_profile_id", new_callable=AsyncMock, return_value="P") as prof:
        await executioner._open_prepared(AsyncMock(), user, ev, "tok", "M-1")
    assert "Warmup prepared connection for 'Mara Lind' ('League match', DECLINE)" in caplog.text
    stored = executioner._PREPARED[ev.id]
    assert (stored.token, stored.recipient_id, stored.accepted, stored.spond_event_id) == ("tok", "M-1", False, "SP-9")
    prof.assert_awaited_once()
    await stored.close()


@pytest.mark.asyncio
async def test_open_prepared_relogs_in_on_a_rejected_token():
    ev = MagicMock(id=uuid.uuid4(), user_choice="accept", invite_time=OPEN, spond_event_id="SP-9")
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
