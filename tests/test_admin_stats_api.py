import uuid
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_stats_returns_zeros_on_empty_db(admin_client, test_db):
    resp = await admin_client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_users"] == 0
    assert data["total_events"] == 0
    assert data["events_pending"] == 0
    assert data["events_processed"] == 0
    assert data["events_failed"] == 0
    assert data["recent_failures"] == []
    # last_discovery_at may be None or a datetime string
    assert "last_discovery_at" in data


@pytest.mark.asyncio
async def test_stats_counts_active_users(admin_client, test_db):
    from app.models.user import User
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="Test User",
        login="test@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    assert resp.json()["active_users"] == 1


@pytest.mark.asyncio
async def test_stats_counts_events_by_status(admin_client, test_db):
    from app.models.user import User
    from app.models.event import Event, STATUS_PENDING, STATUS_PROCESSED, STATUS_FAILED
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="User2",
        login="user2@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    for status in [STATUS_PENDING, STATUS_PROCESSED, STATUS_PROCESSED, STATUS_FAILED]:
        test_db.add(Event(
            id=uuid.uuid4(),
            spond_event_id=f"EVT-{uuid.uuid4().hex[:8]}",
            user_id=user.id,
            heading="Test Event",
            status=status,
        ))
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    data = resp.json()
    assert data["total_events"] == 4
    assert data["events_pending"] == 1
    assert data["events_processed"] == 2
    assert data["events_failed"] == 1


@pytest.mark.asyncio
async def test_stats_recent_failures(admin_client, test_db):
    from app.models.user import User
    from app.models.event import Event, STATUS_FAILED
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="FailUser",
        login="fail@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    test_db.add(Event(
        id=uuid.uuid4(),
        spond_event_id="EVT-FAIL",
        user_id=user.id,
        heading="Broken Event",
        status=STATUS_FAILED,
        error_message="Network timeout",
    ))
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    data = resp.json()
    assert data["events_failed"] == 1
    assert len(data["recent_failures"]) == 1
    assert data["recent_failures"][0]["user_display_name"] == "FailUser"
    assert data["recent_failures"][0]["error_message"] == "Network timeout"
