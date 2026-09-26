# tests/test_static_files.py — the catch-all route serves frontend assets.
# It must only ever serve known web files from frontend/, never anything else,
# and fall back to the sign-in page for every other path.
import pytest
from httpx import ASGITransport, AsyncClient

SIGN_IN_MARKER = 'id="signin-form"'


@pytest.fixture
async def client():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
@pytest.mark.parametrize("path,marker,content_type", [
    ("/app.js", "function apiJson", "javascript"),
    ("/core.js", "CHOICE_LABELS", "javascript"),
    ("/member.css", "--accent", "text/css"),
    ("/join.html", 'id="join-form"', "text/html"),
])
async def test_serves_known_assets(client, path, marker, content_type):
    resp = await client.get(path)
    assert resp.status_code == 200
    assert marker in resp.text
    assert content_type in resp.headers["content-type"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/../app/config.py",
    "/..%2Fapp%2Fconfig.py",
    "/%2e%2e/%2e%2e/etc/passwd",
    "/frontend/../app/main.py",
    "/static/../../app/main.py",
    "/....//app/config.py",
    "/%252e%252e/app/config.py",
])
async def test_traversal_attempts_get_the_sign_in_page(client, path):
    resp = await client.get(path)
    assert resp.status_code in (200, 404)
    assert "BaseSettings" not in resp.text
    assert "root:" not in resp.text
    assert "FastAPI(" not in resp.text


@pytest.mark.asyncio
async def test_non_web_files_in_frontend_are_not_served(client):
    resp = await client.get("/README.md")
    assert resp.status_code == 200
    assert SIGN_IN_MARKER in resp.text
    assert "# SpondBot Frontend" not in resp.text


@pytest.mark.asyncio
async def test_unknown_paths_fall_back_to_sign_in(client):
    resp = await client.get("/some/unknown/page")
    assert resp.status_code == 200
    assert SIGN_IN_MARKER in resp.text


@pytest.mark.asyncio
async def test_no_raw_static_mount_exposes_the_frontend_folder(client):
    resp = await client.get("/static/README.md")
    assert "# SpondBot Frontend" not in resp.text
