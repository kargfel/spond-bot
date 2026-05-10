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


async def reschedule_pending_snipers() -> None:
    """Re-schedule sniper jobs on startup (in-memory jobs are lost on restart)."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.event import CHOICE_ACCEPT, STATUS_PENDING, Event
    from app.workers.executioner import schedule_sniper

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Event).where(
                Event.invite_time > now,
                Event.status == STATUS_PENDING,
                Event.user_choice.in_([CHOICE_ACCEPT, "decline"]),
            )
        )
        events = result.scalars().all()

    for event in events:
        schedule_sniper(_scheduler, event)
    if events:
        logger.info("Sniper: rescheduled %d job(s) on startup.", len(events))
