# tests/test_audit_rsvp.py — the audit trail carries everything the old answer log showed:
# who it was for, which answer, how fast, whether it needed a retry, and what went wrong.
# Runs the real _process_event against SQLite rows; only Spond is faked.
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.spond_client import SpondAPIError, SpondAuthError
from app.models.event import Event
from app.models.rsvp_log import RsvpLog
from app.models.user import User
from app.workers import executioner
from tests.audit_helpers import only, rows

OPENED = datetime(2026, 10, 5, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def world(audit_on, test_engine, test_db, monkeypatch):
    spond = User(id=uuid.uuid4(), display_name="Mara Lind", login="m@example.com", encrypted_password="x", profile_id="P1")
    event = Event(id=uuid.uuid4(), spond_event_id="SP-9", user_id=spond.id, heading="League match", user_choice="accept",
                  status="pending", invite_time=OPENED)
    test_db.add_all([spond, event])
    await test_db.commit()
    monkeypatch.setattr(executioner, "AsyncSessionLocal", async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False))
    return SimpleNamespace(spond=spond, event=event, db=test_db)


def submitted(ms_after_opening: int) -> datetime:
    return OPENED + timedelta(milliseconds=ms_after_opening)


@pytest.mark.asyncio
async def test_a_sent_answer_says_who_what_and_how_fast(world):
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=submitted(41)):
        await executioner._process_event(world.event)
    row = await only(world.db, "rsvp.sent")
    assert (row.actor_type, row.outcome, row.target_label) == ("system", "success", "League match")
    assert row.details == {"choice": "accept", "member": "Mara Lind", "spond_user_id": str(world.spond.id),
                           "spond_event_id": "SP-9", "latency_ms": 41}


@pytest.mark.asyncio
async def test_an_answer_that_needed_a_retry_says_so(world):
    sends = AsyncMock(side_effect=[SpondAuthError("token expired"), submitted(112)])
    with patch.object(executioner, "_submit_rsvp", sends):
        await executioner._process_event(world.event)
    row = await only(world.db, "rsvp.sent")
    assert (row.details["latency_ms"], row.details["retries"]) == (112, 1)
    assert sends.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_answer_keeps_the_reason_and_the_retry_count(world):
    sends = AsyncMock(side_effect=[SpondAuthError("expired"), SpondAPIError("403 member not found")])
    with patch.object(executioner, "_submit_rsvp", sends):
        await executioner._process_event(world.event)
    row = await only(world.db, "rsvp.failed")
    assert row.outcome == "failed"
    assert "403 member not found" in row.details["error"]
    assert row.details["retries"] == 1 and row.details["latency_ms"] is None
    assert row.details["member"] == "Mara Lind"


@pytest.mark.asyncio
async def test_a_plain_failure_has_no_retry_entry(world):
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, side_effect=SpondAPIError("boom")):
        await executioner._process_event(world.event)
    row = await only(world.db, "rsvp.failed")
    assert "retries" not in row.details and row.details["error"] == "boom"


@pytest.mark.asyncio
async def test_a_member_without_a_profile_is_recorded_as_failed(world):
    world.spond.profile_id = None
    await world.db.commit()
    await executioner._process_event(world.event)
    row = await only(world.db, "rsvp.failed")
    assert row.details["error"] == "User not found or missing profile_id."
    assert row.details["member"] == "Mara Lind"


@pytest.mark.asyncio
async def test_the_statistics_table_is_still_written(world):
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=submitted(41)):
        await executioner._process_event(world.event)
    world.db.expire_all()
    (log,) = (await world.db.execute(select(RsvpLog))).scalars().all()
    assert (log.outcome, log.choice, log.retry_count) == ("success", "accept", 0)


@pytest.mark.parametrize("submitted_at,invite,expected", [
    (OPENED + timedelta(milliseconds=38), OPENED, 38),
    (OPENED, OPENED + timedelta(milliseconds=150), -150),               # fired early: negative, not hidden
    (datetime(2026, 10, 5, 18, 0, 0, 90000), datetime(2026, 10, 5, 18, 0, 0), 90),  # naive (SQLite) counts as UTC
    (None, OPENED, None),
    (OPENED, None, None),
])
def test_latency_is_measured_from_registration_opening(submitted_at, invite, expected):
    assert executioner._latency_ms(submitted_at, invite) == expected


@pytest.mark.asyncio
async def test_the_member_can_be_found_by_name_in_the_search(world, audit_on):
    from tests.audit_helpers import client_factory, make_login

    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=submitted(41)):
        await executioner._process_event(world.event)
    admin = await make_login(world.db, "admin", admin=True)
    async with client_factory(world.db)(admin) as c:
        hit = (await c.get("/api/v1/admin/audit", params={"q": "mara lind"})).json()["items"]
        miss = (await c.get("/api/v1/admin/audit", params={"q": "someone else"})).json()["items"]
    assert [i["action"] for i in hit] == ["rsvp.sent"] and miss == []
