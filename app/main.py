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

import bcrypt
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select

from app.api import admin as admin_router
from app.api import auth as auth_router
from app.api import events as events_router
from app.api import users as users_router
from app.config import settings
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
            hashed_password=bcrypt.hashpw(settings.admin_password[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
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
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Spond Multi-User Bot",
    description=(
        "Headless backend for automating Spond RSVP responses across multiple users. "
        "Frontend routes use HttpOnly cookies; internal routes use `Authorization: Bearer <API_KEY>`."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# Register slowapi state and its 429 exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS is intentionally omitted — the frontend is served from the same origin.

# API Routes
app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(users_router.router, prefix="/api/v1")
app.include_router(events_router.router, prefix="/api/v1")
app.include_router(admin_router.router, prefix="/api/v1")

# ── Frontend Serving ────────────────────────────────────────────────

# Mount frontend directory for static assets (css, js, images…)
if _FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


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


# Catch-all for assets (style.css, app.js) — sandboxed to the frontend dir
@app.get("/{path:path}")
async def catch_all(path: str):
    # Resolve to an absolute path and verify it stays inside frontend/
    try:
        target = (_FRONTEND_DIR / path).resolve()
    except Exception:
        return FileResponse(_FRONTEND_DIR / "index.html")

    # Reject any path that escapes the frontend directory (path traversal)
    if not target.is_relative_to(_FRONTEND_DIR):
        return FileResponse(_FRONTEND_DIR / "index.html")

    if target.is_file():
        return FileResponse(target)
    return FileResponse(_FRONTEND_DIR / "index.html")
