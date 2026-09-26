import pytest


@pytest.mark.asyncio
async def test_list_scheduler_jobs_empty(admin_client):
    resp = await admin_client.get("/api/v1/admin/scheduler")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_cancel_nonexistent_job(admin_client):
    resp = await admin_client.delete("/api/v1/admin/scheduler/sniper_00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 204  # idempotent — no error if already gone


@pytest.mark.asyncio
async def test_fire_invalid_job_id(admin_client):
    resp = await admin_client.post("/api/v1/admin/scheduler/not-a-sniper-id/fire")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_fire_sniper_invalid_uuid(admin_client):
    resp = await admin_client.post("/api/v1/admin/scheduler/sniper_not-a-uuid/fire")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cancel_invalid_job_id(admin_client):
    resp = await admin_client.delete("/api/v1/admin/scheduler/not-a-sniper-id")
    assert resp.status_code == 400
