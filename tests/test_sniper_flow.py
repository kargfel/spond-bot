# The whole answer path on real rows (SQLite): warmup leftovers -> sniper -> Spond -> event, answer
# log, audit trail. Only the HTTP calls to Spond are faked, so state transitions, retries, the claim
# between sniper and executioner, and what ends up in the database are exercised for real.
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.spond_client import SpondAPIError, SpondAuthError
from app.models.event import Event
from app.models.rsvp_log import RsvpLog
from app.models.user import User
from app.workers import executioner
from tests.audit_helpers import only, rows

OPEN = datetime.now(timezone.utc) - timedelta(seconds=1)


@pytest.fixture
async def world(tmp_path, monkeypatch):
    """A file-backed SQLite database, so that concurrent sessions (sniper, executioner, audit) get their
    own connections like they do on Postgres; the shared in-memory test database cannot do that."""
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings
    from app.database import Base
    from app.services import audit

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'flow.db'}", connect_args={"timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    test_db = factory()

    monkeypatch.setattr(executioner, "AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "audit_enabled", True)

    @asynccontextmanager
    async def audit_session():
        async with factory() as session:
            yield session

    monkeypatch.setattr(audit, "open_session", audit_session)
    executioner._PREPARED.clear()
    executioner._INFLIGHT.clear()

    real_sleep = asyncio.sleep
    sleeps = []

    async def no_wait(s):
        sleeps.append(s)
        await real_sleep(0)                                  # retries do not wait, but others still get to run

    monkeypatch.setattr(executioner.asyncio, "sleep", no_wait)

    def add(name="Mara Lind", choice="accept", *, active=True, status="pending", invite=OPEN, recipient=None):
        user = User(id=uuid.uuid4(), display_name=name, login=f"{name}@example.com", encrypted_password="x",
                    profile_id=f"P-{name}", is_active=active)
        event = Event(id=uuid.uuid4(), spond_event_id=f"SP-{name}", user_id=user.id, heading="League match",
                      user_choice=choice, status=status, invite_time=invite, resolved_recipient_id=recipient)
        test_db.add_all([user, event])
        return user, event

    async def reload(event):
        await test_db.refresh(event)
        return event

    async def log_rows(event=None):
        q = select(RsvpLog).execution_options(populate_existing=True)
        if event is not None:
            q = q.where(RsvpLog.event_id == event.id)
        return (await test_db.execute(q)).scalars().all()

    yield SimpleNamespace(db=test_db, add=add, reload=reload, log_rows=log_rows, sleeps=sleeps, factory=factory,
                          real_sleep=real_sleep)
    await test_db.close()
    await engine.dispose()


def prepare(event, *, accepted=True, recipient="M-1"):
    http = MagicMock()
    http.close = AsyncMock()
    p = executioner._Prepared(http=http, token="tok", spond_event_id=event.spond_event_id, recipient_id=recipient,
                              accepted=accepted, invite_time=event.invite_time, created=time.monotonic())
    executioner._PREPARED[event.id] = p
    return p


def sent_calls(rsvp):
    return [c.args[2] for c in rsvp.await_args_list]          # spond event id per PUT


# --- the fast path ------------------------------------------------------------

@pytest.mark.asyncio
async def test_prepared_answer_ends_as_processed_with_log_and_audit(world):
    user, event = world.add()
    await world.db.commit()
    prepare(event)
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp):
        await executioner.run_sniper(event.id)

    assert rsvp.await_count == 1
    event = await world.reload(event)
    assert (event.status, event.error_message) == ("processed", None)
    [log] = await world.log_rows(event)
    assert (log.outcome, log.retry_count, log.choice) == ("success", 0, "accept")
    assert log.submitted_at is not None and log.fired_at <= log.submitted_at
    row = await only(world.db, "rsvp.sent")
    assert row.details["prepared"] is True and row.details["attempts"] == 1
    assert {"fire_ms", "prep_ms", "request_ms", "response_ms"} <= set(row.details)


@pytest.mark.asyncio
async def test_prepared_decline_sends_false(world):
    user, event = world.add(choice="decline")
    await world.db.commit()
    prepare(event, accepted=False)
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp):
        await executioner.run_sniper(event.id)
    assert rsvp.await_args.args[4] is False


@pytest.mark.asyncio
async def test_prepared_send_hitting_a_server_error_is_retried_and_logged_as_retry_success(world):
    user, event = world.add(recipient="M-1")
    await world.db.commit()
    prepare(event)
    rsvp = AsyncMock(side_effect=[SpondAPIError("busy", 503), None])
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"):
        await executioner.run_sniper(event.id)
    assert rsvp.await_count == 2
    event = await world.reload(event)
    assert event.status == "processed"
    [log] = await world.log_rows(event)
    assert (log.outcome, log.retry_count) == ("retry_success", 1)
    row = await only(world.db, "rsvp.sent")
    assert row.details["retries"] == 1 and row.details["attempts"] == 2


@pytest.mark.asyncio
async def test_prepared_send_rejected_with_401_logs_in_again_and_reuses_the_member_id(world):
    user, event = world.add()
    await world.db.commit()
    prepare(event, recipient="M-77")
    rsvp = AsyncMock(side_effect=[SpondAuthError("expired"), None])
    relogin = AsyncMock(return_value="fresh-token")
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", relogin), \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock) as bulk:
        await executioner.run_sniper(event.id)
    assert relogin.await_args.kwargs["force"] is True
    bulk.assert_not_called()                                   # no lookups: the member ID is known
    assert rsvp.await_args_list[1].args[1:4] == ("fresh-token", "SP-Mara Lind", "M-77")
    assert (await world.reload(event)).status == "processed"


@pytest.mark.asyncio
async def test_a_refusal_ends_as_failed_with_the_reason_in_event_log_and_audit(world):
    user, event = world.add(recipient="M-1")
    await world.db.commit()
    prepare(event)
    rsvp = AsyncMock(side_effect=SpondAPIError("403 member not found", 403))
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"):
        await executioner.run_sniper(event.id)
    assert rsvp.await_count == 4                               # first try + 3 quick retries, then final
    event = await world.reload(event)
    assert event.status == "failed" and "member not found" in event.error_message
    [log] = await world.log_rows(event)
    assert log.outcome == "failed" and log.submitted_at is None
    row = await only(world.db, "rsvp.failed")
    assert "member not found" in row.details["error"]


@pytest.mark.asyncio
async def test_a_failed_event_can_be_answered_again_after_the_member_re_arms_it(world):
    user, event = world.add(status="failed")
    await world.db.commit()
    event.status = "pending"                                   # what PATCH /events does for a failed event
    await world.db.commit()
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"), \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock, return_value=[{"id": "x"}]), \
         patch.object(executioner.spond_client, "resolve_recipient_id", new_callable=AsyncMock, return_value="M-2"):
        await executioner.run_sniper(event.id)
    assert (await world.reload(event)).status == "processed"


# --- the normal path (no warmup data) -----------------------------------------

@pytest.mark.asyncio
async def test_without_prepared_data_the_sniper_resolves_and_sends(world):
    user, event = world.add()
    await world.db.commit()
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"), \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock, return_value=[{"id": "x"}]), \
         patch.object(executioner.spond_client, "resolve_recipient_id", new_callable=AsyncMock, return_value="M-9"):
        await executioner.run_sniper(event.id)
    assert rsvp.await_args.args[3] == "M-9"
    row = await only(world.db, "rsvp.sent")
    assert row.details["prepared"] is False


@pytest.mark.asyncio
async def test_a_warmup_cached_member_id_skips_the_lookups(world):
    user, event = world.add(recipient="M-CACHED")
    await world.db.commit()
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"), \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock) as bulk:
        await executioner.run_sniper(event.id)
    bulk.assert_not_called()
    assert rsvp.await_args.args[3] == "M-CACHED"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,status", [("manual", "pending"), ("accept", "processed")])
async def test_nothing_is_sent_for_an_event_that_is_no_longer_armed(world, choice, status):
    user, event = world.add(choice=choice, status=status)
    await world.db.commit()
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp):
        await executioner.run_sniper(event.id)
    rsvp.assert_not_called()
    assert await world.log_rows() == []


@pytest.mark.asyncio
async def test_a_member_who_disarmed_during_the_head_start_is_not_answered(world):
    user, event = world.add(recipient="M-1")
    await world.db.commit()
    prepare(event)
    fire_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    with patch.object(executioner, "_sleep_until", new_callable=AsyncMock) as wait, \
         patch.object(executioner.spond_client, "rsvp", new_callable=AsyncMock) as rsvp:
        async def disarm(_):
            event.user_choice = "manual"
            await world.db.commit()
            executioner.cancel_sniper(MagicMock(), event.id)   # what PATCH /events does
            await world.real_sleep(0)
        wait.side_effect = disarm
        await executioner.run_sniper(event.id, fire_at)
    rsvp.assert_not_called()
    assert (await world.reload(event)).status == "pending"


# --- sniper and executioner at the same moment --------------------------------

@pytest.mark.asyncio
async def test_sniper_and_executioner_together_answer_every_event_exactly_once(world):
    members = [world.add(f"M{i}", recipient=f"R{i}") for i in range(6)]
    await world.db.commit()
    for _, ev in members:
        prepare(ev, recipient=f"R-{ev.spond_event_id}")
    rsvp = AsyncMock()

    async def slow_put(*a):
        await world.real_sleep(0.01)                            # a real network wait: everyone interleaves
        await rsvp(*a)

    with patch.object(executioner.spond_client, "rsvp", AsyncMock(side_effect=slow_put)), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"):
        await asyncio.gather(
            executioner.run_executioner(),                      # the minute job, same second
            *[executioner.run_sniper(ev.id) for _, ev in members],
        )
    assert sorted(sent_calls(rsvp)) == sorted(ev.spond_event_id for _, ev in members)   # once each
    for _, ev in members:
        assert (await world.reload(ev)).status == "processed"
    assert len(await world.log_rows()) == 6
    assert len(await rows(world.db, action="rsvp.sent")) == 6


@pytest.mark.asyncio
async def test_executioner_leaves_events_a_sniper_is_working_on_alone(world):
    user, event = world.add()
    await world.db.commit()
    executioner._INFLIGHT.add(event.id)
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp):
        await executioner.run_executioner()
    rsvp.assert_not_called()
    assert (await world.reload(event)).status == "pending"


# --- the executioner as the safety net -----------------------------------------

@pytest.mark.asyncio
async def test_executioner_answers_overdue_events_but_not_the_wrong_ones(world):
    _, due = world.add("Due")
    _, manual = world.add("Manual", choice="manual")
    _, future = world.add("Future", invite=datetime.now(timezone.utc) + timedelta(hours=1))
    _, done = world.add("Done", status="processed")
    _, inactive = world.add("Inactive", active=False)
    await world.db.commit()
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"), \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock, return_value=[{"id": "x"}]), \
         patch.object(executioner.spond_client, "resolve_recipient_id", new_callable=AsyncMock, return_value="M-1"):
        await executioner.run_executioner()
    assert sent_calls(rsvp) == ["SP-Due"]
    assert (await world.reload(due)).status == "processed"
    for ev in (manual, future, inactive):
        assert (await world.reload(ev)).status == "pending"


@pytest.mark.asyncio
async def test_one_failing_member_does_not_stop_the_others(world):
    _, bad = world.add("Bad")
    _, good = world.add("Good")
    await world.db.commit()

    async def put(http, token, event_id, recipient, accepted):
        if event_id == "SP-Bad":
            raise SpondAPIError("403 nope", 403)

    with patch.object(executioner.spond_client, "rsvp", AsyncMock(side_effect=put)), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"), \
         patch.object(executioner.spond_client, "get_bulk_events", new_callable=AsyncMock, return_value=[{"id": "x"}]), \
         patch.object(executioner.spond_client, "resolve_recipient_id", new_callable=AsyncMock, return_value="M-1"):
        await executioner.run_executioner()
    assert (await world.reload(bad)).status == "failed"
    assert (await world.reload(good)).status == "processed"


@pytest.mark.asyncio
async def test_a_user_without_a_spond_profile_fails_cleanly_instead_of_crashing(world):
    user, event = world.add()
    user.profile_id = None
    await world.db.commit()
    rsvp = AsyncMock()
    with patch.object(executioner.spond_client, "rsvp", rsvp):
        await executioner.run_executioner()
    rsvp.assert_not_called()
    event = await world.reload(event)
    assert event.status == "failed" and "profile_id" in event.error_message


@pytest.mark.asyncio
async def test_a_duplicate_sniper_for_an_event_already_being_answered_does_nothing(world):
    """Discovery re-arms snipers hourly; one that lands inside the head start must not answer twice."""
    user, event = world.add(recipient="M-1")
    await world.db.commit()
    prepare(event)
    fire_at = datetime.now(timezone.utc) + timedelta(milliseconds=50)
    rsvp = AsyncMock()

    async def wait(_):
        await world.real_sleep(0.05)

    with patch.object(executioner, "_sleep_until", side_effect=wait), \
         patch.object(executioner.spond_client, "rsvp", rsvp), \
         patch.object(executioner, "ensure_fresh_token", new_callable=AsyncMock, return_value="tok"):
        await asyncio.gather(executioner.run_sniper(event.id, fire_at), executioner.run_sniper(event.id, fire_at))
    assert rsvp.await_count == 1
    assert len(await world.log_rows(event)) == 1
