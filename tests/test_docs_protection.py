import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def non_admin_client():
    from app.main import app
    from app.api import deps

    async def override_non_admin():
        return {
            "sub": "00000000-0000-0000-0000-000000000002",
            "username": "regular",
            "is_admin": False,
            "linked_user_id": "00000000-0000-0000-0000-000000000099",
        }

    app.dependency_overrides[deps._get_current_user] = override_non_admin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


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
async def test_docs_blocked_for_non_admin(non_admin_client):
    resp = await non_admin_client.get("/docs")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_redoc_accessible_to_admin(admin_client):
    resp = await admin_client.get("/redoc")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_redoc_blocked_without_session():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/redoc")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_redoc_blocked_for_non_admin(non_admin_client):
    resp = await non_admin_client.get("/redoc")
    assert resp.status_code == 403
