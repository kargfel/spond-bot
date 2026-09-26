import asyncio
import pytest
from app.core.event_bus import EventBus


@pytest.mark.asyncio
async def test_admin_publish_reaches_subscriber():
    bus = EventBus()
    q = bus.subscribe_admin()
    await bus.publish_admin("rsvp_fired", {"heading": "Test"})
    event = q.get_nowait()
    assert event.type == "rsvp_fired"
    assert event.data["heading"] == "Test"
    bus.unsubscribe_admin(q)


@pytest.mark.asyncio
async def test_user_publish_reaches_correct_subscriber():
    bus = EventBus()
    q_alice = bus.subscribe_user("alice")
    q_bob = bus.subscribe_user("bob")
    await bus.publish_user("alice", "rsvp_fired", {"heading": "Alice Event"})
    assert q_bob.empty()
    event = q_alice.get_nowait()
    assert event.data["heading"] == "Alice Event"
    bus.unsubscribe_user("alice", q_alice)
    bus.unsubscribe_user("bob", q_bob)


@pytest.mark.asyncio
async def test_unsubscribed_queue_receives_nothing():
    bus = EventBus()
    q = bus.subscribe_admin()
    bus.unsubscribe_admin(q)
    await bus.publish_admin("rsvp_fired", {})
    assert q.empty()
