import uuid
import pytest
from datetime import datetime, timezone, timedelta
from app.models.event import STATUS_PROCESSED


@pytest.mark.asyncio
async def test_charts_empty_db(admin_client):
    resp = await admin_client.get("/api/v1/admin/charts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latency_scatter"] == []
    assert data["daily_success_rate"] == []
    assert data["per_user"] == []


@pytest.mark.asyncio
async def test_charts_with_data(admin_client, test_db):
    from app.models.user import User
    from app.models.event import Event
    from app.models.rsvp_log import RsvpLog
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="TestUser",
        login="u@u.com",
        encrypted_password=encrypt("pass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()

    invite = datetime.now(timezone.utc) - timedelta(days=1)
    event = Event(
        id=uuid.uuid4(),
        spond_event_id="CHART001",
        user_id=user.id,
        heading="Chart Test Event",
        user_choice="accept",
        status=STATUS_PROCESSED,
        invite_time=invite,
        created_at=invite,
        updated_at=invite,
    )
    test_db.add(event)
    await test_db.commit()

    log = RsvpLog(
        id=uuid.uuid4(),
        event_id=event.id,
        user_id=user.id,
        spond_event_id="CHART001",
        choice="accept",
        fired_at=invite,
        submitted_at=invite + timedelta(milliseconds=120),
        outcome="success",
        retry_count=0,
    )
    test_db.add(log)
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/charts?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["latency_scatter"]) == 1
    assert data["latency_scatter"][0]["latency_ms"] == 120
    assert len(data["per_user"]) == 1
    assert data["per_user"][0]["user_name"] == "TestUser"
    assert data["per_user"][0]["total"] == 1
