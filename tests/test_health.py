# tests/test_health.py — /api/v1/health is what Docker's HEALTHCHECK polls,
# so it must return a non-200 status whenever the bot cannot do its job.
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db


def _scheduler(running: bool) -> MagicMock:
    s = MagicMock()
    s.running = running
    return s


@pytest.fixture
async def anon_client(test_db):
    """A client with no session cookie: health must not require login."""
    from app.main import app

    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_ok_without_login(anon_client):
    with patch("app.api.events.get_scheduler", return_value=_scheduler(True)):
        resp = await anon_client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok", "scheduler": "running"}


@pytest.mark.asyncio
async def test_health_503_when_database_unreachable(anon_client, test_db):
    async def broken_execute(*args, **kwargs):
        raise ConnectionError("database is down")

    with patch("app.api.events.get_scheduler", return_value=_scheduler(True)), \
         patch.object(test_db, "execute", side_effect=broken_execute):
        resp = await anon_client.get("/api/v1/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "error"
    assert resp.json()["db"] == "error"


@pytest.mark.asyncio
async def test_health_503_when_scheduler_stopped(anon_client):
    with patch("app.api.events.get_scheduler", return_value=_scheduler(False)):
        resp = await anon_client.get("/api/v1/health")
    assert resp.status_code == 503
    assert resp.json()["scheduler"] == "stopped"


@pytest.mark.asyncio
async def test_join_page_is_served(anon_client):
    resp = await anon_client.get("/join")
    assert resp.status_code == 200
    assert 'id="join-form"' in resp.text
