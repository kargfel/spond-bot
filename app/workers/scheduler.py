"""
APScheduler setup.

The scheduler is started inside the FastAPI lifespan context manager so
it shares the same asyncio event loop as the web server. Both workers
(discovery + executioner) are registered here.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler(timezone=settings.tz)


def start_scheduler() -> None:
    from app.workers.discovery import run_discovery
    from app.workers.executioner import run_executioner

    _scheduler.add_job(
        run_discovery,
        trigger="interval",
        minutes=settings.discovery_interval_minutes,
        id="discovery",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        next_run_time=None,  # don't run immediately on startup
    )
    _scheduler.add_job(
        run_executioner,
        trigger=CronTrigger(second=0, timezone=settings.tz),  # fires at :00 of every minute
        id="executioner",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=15,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started. Discovery every %dm, Executioner every %ds.",
        settings.discovery_interval_minutes,
        settings.executioner_interval_seconds,
    )


def shutdown_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down.")


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler
