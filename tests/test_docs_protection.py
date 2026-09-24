import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_docs_accessible_to_admin(admin_client):
    resp = await admin_client.get("/docs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_docs_blocked_without_session():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/docs")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_redoc_accessible_to_admin(admin_client):
    resp = await admin_client.get("/redoc")
    assert resp.status_code == 200
