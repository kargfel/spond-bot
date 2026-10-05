"""
FastAPI application entry point.

Lifecycle:
  startup  → APScheduler starts + admin account is seeded if missing
  shutdown → scheduler is stopped gracefully, DB engine disposed

API docs are available at /docs (Swagger UI) and /redoc.
"""
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select

from app.api import accounts as accounts_router
from app.api.deps import AdminDep
from app.api import admin as admin_router
from app.api import audit as audit_router
from app.api import auth as auth_router
from app.api import stream as stream_router
from app.api import events as events_router
from app.api import invites as invites_router
from app.api import push as push_router
from app.api import users as users_router
from app.config import settings
from app.core.security import hash_password
from app.services.audit import AuditMiddleware
from app.workers.scheduler import reschedule_pending_snipers, shutdown_scheduler, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Resolved path to the frontend directory — used for sandboxed file serving
_FRONTEND_DIR = Path("frontend").resolve()


async def _seed_admin() -> None:
    """
    Create the admin FrontendUser on first startup if none exists yet.
    Credentials come from ADMIN_USERNAME / ADMIN_PASSWORD env vars.
    """
    from app.database import get_db
    from app.models.frontend_user import FrontendUser

    async for db in get_db():
        result = await db.execute(
            select(FrontendUser).where(FrontendUser.is_admin == True).limit(1)  # noqa: E712
        )
        if result.scalar_one_or_none():
            logger.info("Admin account already exists — skipping seed.")
            return

        admin = FrontendUser(
            id=uuid.uuid4(),
            username=settings.admin_username,
            hashed_password=hash_password(settings.admin_password),
            is_admin=True,
            linked_user_id=None,
        )
        db.add(admin)
        await db.commit()
        logger.info(
            "Seeded admin account: username=%r (change ADMIN_PASSWORD in .env!)",
            settings.admin_username,
        )
        break


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Spond Multi-User Bot...")
    await _seed_admin()
    start_scheduler()
    await reschedule_pending_snipers()
    yield
    logger.info("Shutting down...")
    shutdown_scheduler()
    from app.database import engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Rate limiter — shared instance; routers attach @_limiter.limit() decorators
# ---------------------------------------------------------------------------
from app.core.rate_limit import limiter  # noqa: E402

app = FastAPI(
    title="Spond Multi-User Bot",
    description=(
        "Headless backend for automating Spond RSVP responses across multiple users. "
        "Frontend routes use HttpOnly cookies; internal routes use `Authorization: Bearer <API_KEY>`."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# Register slowapi state and its 429 exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Audit trail: request context for every audited action (see app/services/audit.py)
app.add_middleware(AuditMiddleware)

# CORS is intentionally omitted — the frontend is served from the same origin.

# API Routes
app.include_router(accounts_router.router, prefix="/api/v1")
app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(users_router.router, prefix="/api/v1")
app.include_router(events_router.router, prefix="/api/v1")
app.include_router(admin_router.router, prefix="/api/v1")
app.include_router(stream_router.router, prefix="/api/v1")
app.include_router(invites_router.router, prefix="/api/v1")
app.include_router(push_router.router, prefix="/api/v1")
app.include_router(audit_router.router, prefix="/api/v1")

# ── Frontend Serving ────────────────────────────────────────────────


@app.get("/")
@app.get("/login")
@app.get("/index.html")
async def serve_index():
    return FileResponse(_FRONTEND_DIR / "index.html")


@app.get("/dashboard")
@app.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse(_FRONTEND_DIR / "dashboard.html")


@app.get("/admin")
@app.get("/admin.html")
async def serve_admin():
    return FileResponse(_FRONTEND_DIR / "admin.html")


@app.get("/join")
@app.get("/join.html")
async def serve_join():
    return FileResponse(_FRONTEND_DIR / "join.html")


# PWA files that browsers must revalidate on every use: a stale service worker or manifest
# would pin members to an old version. The worker also has to sit at the root so its scope
# covers the whole app.
_REVALIDATE = {"Cache-Control": "no-cache"}


@app.get("/sw.js", include_in_schema=False)
async def serve_service_worker():
    return FileResponse(_FRONTEND_DIR / "sw.js", media_type="text/javascript", headers=_REVALIDATE)


@app.get("/sw-core.js", include_in_schema=False)
async def serve_service_worker_core():
    return FileResponse(_FRONTEND_DIR / "sw-core.js", media_type="text/javascript", headers=_REVALIDATE)


@app.get("/manifest.webmanifest", include_in_schema=False)
async def serve_manifest():
    return FileResponse(
        _FRONTEND_DIR / "manifest.webmanifest", media_type="application/manifest+json", headers=_REVALIDATE
    )


@app.get("/docs", include_in_schema=False)
async def protected_docs(current_user: dict = AdminDep):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="SpondBot API")


@app.get("/redoc", include_in_schema=False)
async def protected_redoc(current_user: dict = AdminDep):
    return get_redoc_html(openapi_url="/openapi.json", title="SpondBot API")


# Web assets the catch-all may serve, collected once at startup. A request path
# is only ever used as a key into this map, never to build a filesystem path,
# so nothing outside it (source, README, dotfiles) can be reached.
_ASSET_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2", ".webmanifest"}
_ASSETS: dict[str, Path] = (
    {
        p.relative_to(_FRONTEND_DIR).as_posix(): p
        for p in _FRONTEND_DIR.rglob("*")
        if p.is_file() and p.suffix in _ASSET_SUFFIXES and not p.name.startswith(".")
    }
    if _FRONTEND_DIR.is_dir()
    else {}
)


# Catch-all for assets (css, js); anything unknown gets the sign-in page.
@app.get("/{path:path}")
async def catch_all(path: str):
    asset = _ASSETS.get(path)
    if asset is None:
        return FileResponse(_FRONTEND_DIR / "index.html")
    return FileResponse(asset)
