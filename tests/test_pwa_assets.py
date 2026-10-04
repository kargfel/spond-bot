# tests/test_pwa_assets.py — the web app is installable: manifest, icons, service worker,
# offline page and self-hosted fonts are served correctly, and every page links them.
import json
import re
import struct
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
PAGES = ["index.html", "join.html", "dashboard.html", "admin.html"]


@pytest.fixture
async def client():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", data[16:24])


# ── Manifest ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_is_served_as_a_web_manifest_and_always_revalidated(client):
    resp = await client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    assert resp.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_manifest_has_what_browsers_need_to_offer_installation(client):
    m = (await client.get("/manifest.webmanifest")).json()
    assert m["name"] == "SpondBot" and m["short_name"] == "SpondBot"
    assert m["display"] == "standalone"
    assert m["scope"] == "/" and m["start_url"].startswith("/")
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", m["theme_color"])
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", m["background_color"])
    purposes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert ("192x192", "any") in purposes
    assert ("512x512", "any") in purposes
    assert ("512x512", "maskable") in purposes


@pytest.mark.asyncio
async def test_every_manifest_icon_is_served_and_matches_its_declared_size(client):
    m = (await client.get("/manifest.webmanifest")).json()
    for icon in m["icons"]:
        resp = await client.get(icon["src"])
        assert resp.status_code == 200, icon["src"]
        assert resp.headers["content-type"] == "image/png"
        w, h = (int(n) for n in icon["sizes"].split("x"))
        assert png_size(resp.content) == (w, h), icon["src"]


def test_manifest_file_is_valid_json():
    json.loads((FRONTEND / "manifest.webmanifest").read_text())


# ── Service worker ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/sw.js", "/sw-core.js"])
async def test_service_worker_scripts_are_javascript_and_never_cached(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_service_worker_handles_fetch_push_and_notification_clicks(client):
    sw = (await client.get("/sw.js")).text
    for event in ("fetch", "push", "notificationclick", "pushsubscriptionchange", "message"):
        assert f'addEventListener("{event}"' in sw, event


@pytest.mark.asyncio
async def test_service_worker_never_touches_the_api(client):
    core = (await client.get("/sw-core.js")).text
    assert '"/api/"' in core


# ── Pages ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("page", PAGES)
async def test_every_page_links_manifest_icons_and_theme_color(client, page):
    html = (FRONTEND / page).read_text()
    assert '<link rel="manifest" href="/manifest.webmanifest"' in html
    assert 'rel="apple-touch-icon" href="/icons/apple-touch-icon.png"' in html
    assert 'rel="icon"' in html
    assert 'name="theme-color"' in html
    assert 'name="apple-mobile-web-app-capable"' in html
    assert "viewport-fit=cover" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("page", PAGES)
async def test_pages_use_self_hosted_fonts_not_google(client, page):
    html = (FRONTEND / page).read_text()
    assert "/fonts/fonts.css" in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


@pytest.mark.asyncio
@pytest.mark.parametrize("page", PAGES)
async def test_icons_linked_by_pages_exist(client, page):
    html = (FRONTEND / page).read_text()
    for href in re.findall(r'href="(/icons/[^"]+)"', html):
        assert (await client.get(href)).status_code == 200, href


@pytest.mark.asyncio
async def test_pages_register_the_service_worker_through_app_js(client):
    app_js = (await client.get("/app.js")).text
    assert 'register("/sw.js"' in app_js
    for page in PAGES:
        assert "app.js" in (FRONTEND / page).read_text()


# ── Fonts ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_font_stylesheet_and_every_font_file_are_served(client):
    css = await client.get("/fonts/fonts.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    urls = re.findall(r"url\((/fonts/[^)]+\.woff2)\)", css.text)
    assert len(urls) >= 9
    for url in urls:
        resp = await client.get(url)
        assert resp.status_code == 200, url
        assert resp.content[:4] == b"wOF2", url
    families = set(re.findall(r"font-family: '([^']+)'", css.text))
    assert {"Public Sans", "Barlow Condensed", "IBM Plex Mono", "IBM Plex Sans Condensed"} <= families


# ── Offline page ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_page_is_served_and_depends_only_on_local_files(client):
    resp = await client.get("/offline.html")
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "SpondBot" in html
    assert not re.search(r'(?:src|href)="https?://', html), "offline page must not load remote files"
    for path in re.findall(r'(?:src|href)="(/[^"?]+)', html):
        assert (await client.get(path)).status_code == 200, path
