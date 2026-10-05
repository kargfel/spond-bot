"""
Security response headers and request size limit, as pure ASGI middleware.

SecurityHeadersMiddleware adds, to every response (never overriding a header a handler set):
  - Content-Security-Policy: scripts only from this site (plus jsDelivr for Chart.js, which is
    pinned with SRI), nothing may frame the app, forms and base URIs stay on this site.
    /docs and /redoc (admin only, Swagger UI) get a looser policy because they load their UI
    from a CDN and use an inline bootstrap script.
  - X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, COOP/CORP.
  - Strict-Transport-Security when the site is served over HTTPS (SITE_DOMAIN is not localhost).
  - Cache-Control: no-store for /api/: member data and sessions must never sit in a shared cache.

BodyLimitMiddleware rejects request bodies over 1 MiB with 413: every real request here is a
few hundred bytes of JSON, so a large body is either a mistake or an attempt to eat memory.
"""
import json

from app.config import settings

CSP = "; ".join([
    "default-src 'self'",
    # Chart.js comes from jsDelivr with an SRI hash; everything else is our own files
    "script-src 'self' https://cdn.jsdelivr.net",
    # Inline style="" attributes are used by the markup; no inline scripts are
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

DOCS_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "frame-ancestors 'none'",
])

DOCS_PATHS = {"/docs", "/redoc"}

STATIC_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
HSTS = (b"strict-transport-security", b"max-age=15552000")  # 180 days; no includeSubDomains/preload on purpose

MAX_BODY_BYTES = 1024 * 1024


class SecurityHeadersMiddleware:
    def __init__(self, app, *, https: bool | None = None):
        self.app = app
        self.https = (settings.site_domain != "localhost") if https is None else https

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        policy = DOCS_CSP if path in DOCS_PATHS else CSP

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                extra = [(b"content-security-policy", policy.encode())] + STATIC_HEADERS
                if self.https:
                    extra.append(HSTS)
                if path.startswith("/api/"):
                    extra.append((b"cache-control", b"no-store"))
                headers.extend((k, v) for k, v in extra if k not in present)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app, *, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = False
            if too_big:
                return await self._reject(send)

        seen = 0
        started = False

        async def counting_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _TooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send):
        body = json.dumps({"detail": "Request body too large."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
