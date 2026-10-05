# tests/test_security_headers.py — what every response carries, what the policy forbids, and
# that oversized request bodies are refused (and recorded).
import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import security_headers as sh
from tests.audit_helpers import client_factory, make_login, only, rows


@pytest.fixture
async def anon():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def csp(resp) -> dict[str, list[str]]:
    return {d.split()[0]: d.split()[1:] for d in resp.headers["content-security-policy"].split("; ")}


# ── headers ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/dashboard", "/admin", "/join", "/app.js", "/offline.html", "/sw.js",
                                  "/manifest.webmanifest", "/api/v1/health", "/api/v1/events", "/nope/whatever"])
async def test_every_response_carries_the_security_headers(anon, path):
    resp = await anon.get(path)
    h = resp.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "same-origin"
    assert "camera=()" in h["permissions-policy"] and "geolocation=()" in h["permissions-policy"]
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert "content-security-policy" in h


@pytest.mark.asyncio
async def test_the_policy_allows_no_inline_scripts_and_no_framing(anon):
    p = csp(await anon.get("/"))
    assert p["default-src"] == ["'self'"]
    assert "'unsafe-inline'" not in p["script-src"] and "'unsafe-eval'" not in p["script-src"]
    assert p["script-src"] == ["'self'", "https://cdn.jsdelivr.net"]
    assert p["frame-ancestors"] == ["'none'"]
    assert p["object-src"] == ["'none'"] and p["base-uri"] == ["'self'"] and p["form-action"] == ["'self'"]
    assert p["connect-src"] == ["'self'"] and p["worker-src"] == ["'self'"]


@pytest.mark.asyncio
async def test_api_answers_are_never_stored_by_caches(anon):
    for path in ("/api/v1/health", "/api/v1/events", "/api/v1/auth/me"):
        assert (await anon.get(path)).headers["cache-control"] == "no-store", path
    # but a header a handler chose is kept, and static files are untouched
    assert (await anon.get("/sw.js")).headers["cache-control"] == "no-cache"
    assert "cache-control" not in (await anon.get("/app.js")).headers or (await anon.get("/app.js")).headers["cache-control"] != "no-store"


@pytest.mark.asyncio
async def test_errors_and_refusals_carry_the_headers_too(anon):
    for resp in (await anon.get("/api/v1/admin/stats"), await anon.post("/api/v1/auth/login", json={}), await anon.get("/docs")):
        assert resp.status_code in (401, 422)
        assert resp.headers["x-frame-options"] == "DENY" and "content-security-policy" in resp.headers


@pytest.mark.asyncio
async def test_the_docs_pages_get_the_looser_policy_they_need(test_db, real_sessions):
    boss = await make_login(test_db, "boss", admin=True)
    async with client_factory(test_db)(boss) as c:
        docs, redoc, home = await c.get("/docs"), await c.get("/redoc"), await c.get("/")
    for resp in (docs, redoc):
        assert resp.status_code == 200
        assert "'unsafe-inline'" in csp(resp)["script-src"] and csp(resp)["frame-ancestors"] == ["'none'"]
    assert "'unsafe-inline'" not in csp(home)["script-src"]


@pytest.mark.asyncio
async def test_https_sites_tell_browsers_to_stay_on_https():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    for https, expected in ((True, True), (False, False)):
        async with AsyncClient(transport=ASGITransport(app=sh.SecurityHeadersMiddleware(app, https=https)), base_url="http://t") as c:
            h = (await c.get("/")).headers
        assert ("strict-transport-security" in h) is expected
        if expected:
            assert h["strict-transport-security"] == "max-age=15552000" and "includeSubDomains" not in h["strict-transport-security"]


@pytest.mark.asyncio
async def test_a_header_set_by_a_handler_is_not_overwritten():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"Content-Security-Policy", b"default-src 'none'"),
                                                                                (b"x-frame-options", b"SAMEORIGIN")]})
        await send({"type": "http.response.body", "body": b""})

    async with AsyncClient(transport=ASGITransport(app=sh.SecurityHeadersMiddleware(app, https=False)), base_url="http://t") as c:
        h = (await c.get("/")).headers
    assert h["content-security-policy"] == "default-src 'none'" and h["x-frame-options"] == "SAMEORIGIN"


def test_the_site_never_needs_an_inline_script_or_handler():
    """The policy forbids them, so none may exist in the pages (or the policy would silently break them)."""
    from pathlib import Path

    for page in Path("frontend").glob("*.html"):
        html = page.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), f"{page.name} has an inline <script>"
        assert not re.search(r"\son[a-z]+\s*=", html), f"{page.name} has an inline event handler"
        assert "javascript:" not in html, page.name


def test_remote_scripts_are_pinned_with_integrity_hashes():
    from pathlib import Path

    for page in Path("frontend").glob("*.html"):
        for tag in re.findall(r"<script[^>]*\ssrc=\"https?://[^>]*>", page.read_text()):
            assert "integrity=" in tag and "crossorigin" in tag, tag


# ── body limit ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused_before_it_is_read(anon):
    resp = await anon.post("/api/v1/auth/login", content=b"x" * (sh.MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
    assert resp.status_code == 413 and resp.json() == {"detail": "Request body too large."}
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_normal_bodies_pass(test_db):
    big_but_fine = json.dumps({"username": "u" * 5000, "password": "p" * 5000}).encode()
    async with client_factory(test_db)(None) as c:
        assert (await c.post("/api/v1/auth/login", content=big_but_fine, headers={"content-type": "application/json"})).status_code == 401


@pytest.mark.asyncio
async def test_a_streamed_body_without_a_length_is_cut_off_too():
    seen = []

    async def app(scope, receive, send):
        while True:
            msg = await receive()
            seen.append(len(msg.get("body", b"")))
            if not msg.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    wrapped = sh.BodyLimitMiddleware(app, max_bytes=100)
    sent = []

    async def send(message):
        sent.append(message)

    chunks = iter([{"type": "http.request", "body": b"a" * 60, "more_body": True},
                   {"type": "http.request", "body": b"b" * 60, "more_body": True},
                   {"type": "http.request", "body": b"c", "more_body": False}])

    async def receive():
        return next(chunks)

    await wrapped({"type": "http", "method": "POST", "path": "/", "headers": []}, receive, send)
    assert sent[0]["status"] == 413 and seen == [60]


@pytest.mark.asyncio
async def test_a_refused_body_is_recorded_in_the_audit_trail(audit_on, test_db):
    async with client_factory(test_db)(None) as c:
        resp = await c.post("/api/v1/auth/login", content=b"x" * (sh.MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
    assert resp.status_code == 413
    row = await only(test_db, "http.post")
    assert (row.outcome, row.status_code, row.path) == ("failed", 413, "/api/v1/auth/login")
    assert len(await rows(test_db)) == 1


# ── page caching ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/login", "/index.html", "/dashboard", "/dashboard.html", "/admin", "/admin.html",
                                  "/join", "/join.html", "/offline.html", "/some/unknown/path"])
async def test_html_pages_are_always_revalidated(anon, path):
    """Pages have no version in their URL; heuristic caching would show members stale pages after a deploy
    and hide a dead server from the service worker."""
    resp = await anon.get(path)
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_versioned_assets_are_not_forced_to_revalidate(anon):
    resp = await anon.get("/app.js")
    assert resp.headers.get("cache-control") != "no-cache"
