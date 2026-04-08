"""
FastAPI application entry point.

Lifecycle:
  startup  → APScheduler starts (discovery + executioner workers)
  shutdown → scheduler is stopped gracefully, DB engine disposed

API docs are available at /docs (Swagger UI) and /redoc.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import events as events_router
from app.api import users as users_router
from app.workers.scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Spond Multi-User Bot...")
    start_scheduler()
    yield
    logger.info("Shutting down...")
    shutdown_scheduler()
    from app.database import engine
    await engine.dispose()


app = FastAPI(
    title="Spond Multi-User Bot",
    description=(
        "Headless backend for automating Spond RSVP responses across multiple users. "
        "All endpoints require `Authorization: Bearer <API_KEY>`."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(users_router.router, prefix="/api/v1")
app.include_router(events_router.router, prefix="/api/v1")
