# Setting a decision must arm or disarm the sniper, and nobody may touch someone else's events.
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.event import Event
from app.models.user import User


async def make_event(db, *, choice="manual", status="pending", error=None, owner=None):
    owner = owner or User(id=uuid.uuid4(), display_name="Mara", login=f"{uuid.uuid4()}@example.com",
                          encrypted_password="x", profile_id="P")
    db.add(owner)
    event = Event(id=uuid.uuid4(), spond_event_id="SP-1", user_id=owner.id, heading="League match",
                  user_choice=choice, status=status, error_message=error,
                  invite_time=datetime.now(timezone.utc) + timedelta(days=1))
    db.add(event)
    await db.commit()
    return owner, event


@pytest.fixture
def sniper():
    with patch("app.api.events.schedule_sniper") as arm, patch("app.api.events.cancel_sniper") as disarm:
        yield arm, disarm


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["accept", "decline"])
async def test_choosing_an_answer_arms_the_sniper(admin_client, test_db, sniper, choice):
    arm, disarm = sniper
    _, event = await make_event(test_db)
    resp = await admin_client.patch(f"/api/v1/events/{event.id}", json={"user_choice": choice})
    assert resp.status_code == 200 and resp.json()["user_choice"] == choice
    arm.assert_called_once()
    assert arm.call_args.args[1].id == event.id
    disarm.assert_not_called()


@pytest.mark.asyncio
async def test_leaving_it_to_me_disarms_the_sniper(admin_client, test_db, sniper):
    arm, disarm = sniper
    _, event = await make_event(test_db, choice="accept")
    resp = await admin_client.patch(f"/api/v1/events/{event.id}", json={"user_choice": "manual"})
    assert resp.status_code == 200
    disarm.assert_called_once()
    assert disarm.call_args.args[1] == event.id
    arm.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_event_becomes_pending_again_and_is_armed(admin_client, test_db, sniper):
    arm, _ = sniper
    _, event = await make_event(test_db, choice="decline", status="failed", error="403 member not found")
    resp = await admin_client.patch(f"/api/v1/events/{event.id}", json={"user_choice": "accept"})
    body = resp.json()
    assert (body["status"], body["error_message"]) == ("pending", None)
    arm.assert_called_once()


@pytest.mark.asyncio
async def test_an_already_answered_event_is_not_armed_again(admin_client, test_db, sniper):
    arm, disarm = sniper
    _, event = await make_event(test_db, choice="accept", status="processed")
    await admin_client.patch(f"/api/v1/events/{event.id}", json={"user_choice": "decline"})
    arm.assert_not_called()
    disarm.assert_called_once()


@pytest.mark.asyncio
async def test_a_failed_event_set_to_manual_stays_failed_and_disarmed(admin_client, test_db, sniper):
    arm, disarm = sniper
    _, event = await make_event(test_db, choice="accept", status="failed", error="x")
    resp = await admin_client.patch(f"/api/v1/events/{event.id}", json={"user_choice": "manual"})
    assert resp.json()["status"] == "failed"
    arm.assert_not_called()
    disarm.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_event_is_404_and_a_bad_choice_is_rejected(admin_client, test_db, sniper):
    assert (await admin_client.patch(f"/api/v1/events/{uuid.uuid4()}", json={"user_choice": "accept"})).status_code == 404
    _, event = await make_event(test_db)
    assert (await admin_client.patch(f"/api/v1/events/{event.id}", json={"user_choice": "maybe"})).status_code == 422
    arm, disarm = sniper
    arm.assert_not_called()


@pytest.mark.asyncio
async def test_members_can_only_change_their_own_events(client_as, test_db, sniper):
    arm, _ = sniper
    owner, mine = await make_event(test_db)
    _, theirs = await make_event(test_db)
    claims = {"sub": str(uuid.uuid4()), "username": "mara", "is_admin": False, "linked_user_id": str(owner.id)}
    async with client_as(claims) as client:
        assert (await client.patch(f"/api/v1/events/{mine.id}", json={"user_choice": "accept"})).status_code == 200
        denied = await client.patch(f"/api/v1/events/{theirs.id}", json={"user_choice": "accept"})
        assert denied.status_code in (403, 404)
        assert (await client.get(f"/api/v1/events/{theirs.id}")).status_code in (403, 404)
        assert [e["id"] for e in (await client.get("/api/v1/events")).json()] == [str(mine.id)]
    arm.assert_called_once()


@pytest.mark.asyncio
async def test_anonymous_and_unlinked_logins_cannot_use_events(client_as, test_db, sniper):
    _, event = await make_event(test_db)
    async with client_as(None) as client:
        assert (await client.patch(f"/api/v1/events/{event.id}", json={"user_choice": "accept"})).status_code == 401
    claims = {"sub": str(uuid.uuid4()), "username": "x", "is_admin": False, "linked_user_id": None}
    async with client_as(claims) as client:
        assert (await client.get("/api/v1/events")).json() == []
