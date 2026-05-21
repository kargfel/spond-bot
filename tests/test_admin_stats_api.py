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


@pytest.mark.asyncio
async def test_stats_timing_metrics_present(admin_client, test_db):
    resp = await admin_client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    data = resp.json()
    # Fields must be present (None when no data)
    assert "rsvp_p50_ms" in data
    assert "rsvp_p95_ms" in data
    assert "rsvp_sample_count" in data
    assert data["rsvp_sample_count"] == 0
    assert data["rsvp_p50_ms"] is None
    assert data["rsvp_p95_ms"] is None


@pytest.mark.asyncio
async def test_stats_timing_metrics_with_data(admin_client, test_db):
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS
    from app.models.user import User
    from app.models.event import Event, STATUS_PROCESSED
    from app.core.security import encrypt
    from datetime import timedelta
    import uuid

    user = User(
        id=uuid.uuid4(),
        display_name="TimingUser",
        login="timing@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    invite_time = datetime.now(timezone.utc) - timedelta(minutes=5)

    for delay_ms in [100, 200, 300, 400, 500]:
        ev = Event(
            id=uuid.uuid4(),
            spond_event_id=f"EVT-T-{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            heading="Timed Event",
            status=STATUS_PROCESSED,
            invite_time=invite_time,
        )
        test_db.add(ev)
        await test_db.flush()

        submitted = invite_time + timedelta(milliseconds=delay_ms)
        test_db.add(RsvpLog(
            id=uuid.uuid4(),
            event_id=ev.id,
            user_id=user.id,
            spond_event_id=ev.spond_event_id,
            choice="accept",
            fired_at=invite_time,
            submitted_at=submitted,
            outcome=OUTCOME_SUCCESS,
            retry_count=0,
        ))

    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    data = resp.json()
    assert data["rsvp_sample_count"] == 5
    # p50 of [100,200,300,400,500] should be around 300ms
    assert data["rsvp_p50_ms"] is not None
    assert 200 <= data["rsvp_p50_ms"] <= 400
