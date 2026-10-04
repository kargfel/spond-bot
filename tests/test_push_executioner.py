# tests/test_push_executioner.py — the RSVP path tells the member's devices what happened.
# Runs the real _process_event against SQLite rows; only Spond and the push service are faked.
import json
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.spond_client import SpondAPIError
from app.models.event import Event
from app.models.frontend_user import FrontendUser
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import push
from app.workers import executioner

FCM = "https://fcm.googleapis.com/fcm/send/felix-phone"


@pytest.fixture
def push_on(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    monkeypatch.setattr(settings, "vapid_private_key", generate())


@pytest.fixture
async def world(test_engine, test_db, monkeypatch):
    """A member with a linked Spond account, one subscribed phone and a due event."""
    spond = User(id=uuid.uuid4(), display_name="Felix", login="f@example.com", encrypted_password="x", profile_id="P1")
    login = FrontendUser(id=uuid.uuid4(), username="felix", hashed_password="x", linked_user_id=spond.id)
    sub = PushSubscription(frontend_user_id=login.id, endpoint=FCM, p256dh="k", auth="a")
    event = Event(
        id=uuid.uuid4(), spond_event_id="SP-1", user_id=spond.id, heading="Training, Hall B", user_choice="accept",
        status="pending", invite_time=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    test_db.add_all([spond, login, sub, event])
    await test_db.commit()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(executioner, "AsyncSessionLocal", factory)
    monkeypatch.setattr(push, "AsyncSessionLocal", factory)
    return SimpleNamespace(event=event, spond=spond, login=login, factory=factory)


async def fire(world):
    await executioner._process_event(world.event)
    await push.wait_for_pending()


@pytest.mark.asyncio
async def test_a_sent_answer_notifies_the_members_phone(world, push_on):
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=datetime.now(timezone.utc)), \
         patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)

    send.assert_awaited_once()
    kw = send.call_args.kwargs
    assert kw["subscription_info"]["endpoint"] == FCM
    assert json.loads(kw["data"]) == {
        "title": "Answer sent: Going", "body": "Training, Hall B",
        "tag": f"rsvp-{world.event.id}", "url": "/dashboard", "outcome": "success",
    }


@pytest.mark.asyncio
async def test_a_declined_answer_says_not_going(world, push_on):
    async with world.factory() as db:
        ev = await db.get(Event, world.event.id)
        ev.user_choice = "decline"
        await db.commit()
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=datetime.now(timezone.utc)), \
         patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)
    assert json.loads(send.call_args.kwargs["data"])["title"] == "Answer sent: Not going"


@pytest.mark.asyncio
async def test_a_failed_answer_notifies_and_asks_for_a_retry(world, push_on):
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, side_effect=SpondAPIError("403 not a member")), \
         patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)

    payload = json.loads(send.call_args.kwargs["data"])
    assert payload["outcome"] == "failed"
    assert payload["title"] == "SpondBot couldn't answer"
    assert "Training, Hall B" in payload["body"]
    assert "403" not in json.dumps(payload), "Spond's error text stays in the app, not in the notification"


@pytest.mark.asyncio
async def test_the_answer_is_recorded_even_if_the_push_service_is_down(world, push_on):
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=datetime.now(timezone.utc)), \
         patch("app.services.push.webpush_async", side_effect=RuntimeError("push service down")):
        await fire(world)

    async with world.factory() as db:
        assert (await db.get(Event, world.event.id)).status == "processed"


@pytest.mark.asyncio
async def test_nothing_is_sent_when_push_is_not_set_up(world, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=datetime.now(timezone.utc)), \
         patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)
    send.assert_not_called()
    async with world.factory() as db:
        assert (await db.get(Event, world.event.id)).status == "processed"


@pytest.mark.asyncio
async def test_an_event_that_was_already_claimed_sends_nothing(world, push_on):
    async with world.factory() as db:
        ev = await db.get(Event, world.event.id)
        ev.status = "processed"
        await db.commit()
    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)
    send.assert_not_called()


@pytest.mark.asyncio
async def test_other_members_are_not_notified(world, push_on):
    async with world.factory() as db:
        other_spond = User(id=uuid.uuid4(), display_name="Mara", login="m@example.com", encrypted_password="x")
        other_login = FrontendUser(id=uuid.uuid4(), username="mara", hashed_password="x", linked_user_id=other_spond.id)
        db.add_all([other_spond, other_login])
        await db.flush()
        db.add(PushSubscription(frontend_user_id=other_login.id, endpoint="https://fcm.googleapis.com/fcm/send/mara", p256dh="k", auth="a"))
        await db.commit()
    with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=datetime.now(timezone.utc)), \
         patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)
    assert [c.kwargs["subscription_info"]["endpoint"] for c in send.call_args_list] == [FCM]


@pytest.mark.asyncio
async def test_a_member_without_a_profile_id_is_told_it_failed(world, push_on):
    async with world.factory() as db:
        user = await db.get(User, world.spond.id)
        user.profile_id = None
        await db.commit()
    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await fire(world)
    assert json.loads(send.call_args.kwargs["data"])["outcome"] == "failed"


@pytest.mark.asyncio
async def test_the_live_stream_still_gets_the_event(world, push_on):
    from app.core.event_bus import bus

    q = bus.subscribe_user(str(world.spond.id))
    try:
        with patch.object(executioner, "_submit_rsvp", new_callable=AsyncMock, return_value=datetime.now(timezone.utc)), \
             patch("app.services.push.webpush_async", new_callable=AsyncMock):
            await fire(world)
        ev = q.get_nowait()
    finally:
        bus.unsubscribe_user(str(world.spond.id), q)
    assert ev.type == "rsvp_fired" and ev.data == {"heading": "Training, Hall B", "choice": "accept", "outcome": "success"}
