import uuid
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_rsvp_log_empty(admin_client, test_db):
    resp = await admin_client.get("/api/v1/admin/rsvp-log")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_rsvp_log_returns_entries(admin_client, test_db):
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS

    log = RsvpLog(
        id=uuid.uuid4(),
        event_id=None,
        user_id=None,
        spond_event_id="EVT001",
        choice="accept",
        fired_at=datetime.now(timezone.utc),
        submitted_at=datetime.now(timezone.utc),
        outcome=OUTCOME_SUCCESS,
        retry_count=0,
        error_detail=None,
    )
    test_db.add(log)
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/rsvp-log")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["spond_event_id"] == "EVT001"
    assert data[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_rsvp_log_limit(admin_client, test_db):
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS

    for i in range(5):
        test_db.add(RsvpLog(
            id=uuid.uuid4(),
            event_id=None,
            user_id=None,
            spond_event_id=f"EVT{i:03d}",
            choice="accept",
            fired_at=datetime.now(timezone.utc),
            submitted_at=None,
            outcome=OUTCOME_SUCCESS,
            retry_count=0,
        ))
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/rsvp-log?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_rsvp_log_requires_admin(test_db):
    from app.main import app
    from app.database import get_db
    from app.api import deps
    from httpx import AsyncClient, ASGITransport

    async def override_get_db():
        yield test_db

    async def non_admin_user():
        return {"sub": "abc", "username": "user", "is_admin": False, "linked_user_id": None}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps._get_current_user] = non_admin_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/rsvp-log")

    app.dependency_overrides.clear()
    assert resp.status_code == 403
