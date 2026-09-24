# tests/test_event_routes.py
import uuid
import pytest
from datetime import datetime, timezone
from app.models.event import STATUS_PENDING


@pytest.mark.asyncio
async def test_patch_event_sets_decision(admin_client, test_db):
    from app.models.user import User
    from app.models.event import Event
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="Tester",
        login="t@t.com",
        encrypted_password=encrypt("pass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()

    event = Event(
        id=uuid.uuid4(),
        spond_event_id="ABCDEF123",
        user_id=user.id,
        heading="Test Event",
        user_choice="manual",
        status=STATUS_PENDING,
        invite_time=datetime(2030, 1, 1),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    test_db.add(event)
    await test_db.commit()

    resp = await admin_client.patch(
        f"/api/v1/events/{event.id}",
        json={"user_choice": "accept"},
    )
    assert resp.status_code == 200
    assert resp.json()["user_choice"] == "accept"


@pytest.mark.asyncio
async def test_admin_sync_endpoint(admin_client):
    resp = await admin_client.post("/api/v1/admin/sync")
    assert resp.status_code == 202
