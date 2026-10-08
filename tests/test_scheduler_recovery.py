# After a restart the in-memory sniper jobs are gone: they must be re-armed for exactly the events
# that still need an answer.
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.user import User
from app.workers import scheduler


@pytest.mark.asyncio
async def test_only_pending_armed_future_events_are_rearmed(test_engine, test_db, monkeypatch):
    user = User(id=uuid.uuid4(), display_name="Mara", login="m@example.com", encrypted_password="x", profile_id="P")
    test_db.add(user)
    soon, past = datetime.now(timezone.utc) + timedelta(hours=3), datetime.now(timezone.utc) - timedelta(hours=3)

    def ev(name, choice, status, invite):
        return Event(id=uuid.uuid4(), spond_event_id=name, user_id=user.id, heading=name, user_choice=choice,
                     status=status, invite_time=invite)

    wanted = [ev("accept-soon", "accept", "pending", soon), ev("decline-soon", "decline", "pending", soon)]
    ignored = [ev("manual", "manual", "pending", soon), ev("done", "accept", "processed", soon),
               ev("failed", "accept", "failed", soon), ev("overdue", "accept", "pending", past),
               ev("no-time", "accept", "pending", None)]
    test_db.add_all(wanted + ignored)
    await test_db.commit()

    monkeypatch.setattr("app.database.AsyncSessionLocal",
                        async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False))
    monkeypatch.setattr(scheduler, "_scheduler", object())
    with patch("app.workers.executioner.schedule_sniper") as arm:
        await scheduler.reschedule_pending_snipers()
    assert sorted(c.args[1].heading for c in arm.call_args_list) == ["accept-soon", "decline-soon"]
