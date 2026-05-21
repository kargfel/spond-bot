"""
Worker B — Executioner (runs every EXECUTIONER_INTERVAL_SECONDS).

Finds all events where:
  - invite_time <= now
  - status = 'pending'
  - user_choice IN ('accept', 'decline')

For each match, submits the RSVP to Spond concurrently using asyncio.gather.
On 401, forces a token refresh and retries once. On other failures, marks
the event as 'failed' with an error_message for visibility.

Sniper jobs (schedule_sniper / cancel_sniper / run_sniper) supplement the
interval-based executioner by scheduling one-shot DateTrigger jobs that fire
at exactly invite_time, providing millisecond-precision RSVP timing.
"""
import asyncio
import contextlib
import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import aiohttp
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import spond_client
from app.core.spond_client import SpondAPIError, SpondAuthError
from app.database import AsyncSessionLocal
from app.models.event import (
    CHOICE_ACCEPT,
    CHOICE_DECLINE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_PROCESSING,
    Event,
)
from app.models.rsvp_log import OUTCOME_FAILED, OUTCOME_RETRY_SUCCESS, OUTCOME_SUCCESS, RsvpLog
from app.models.user import User
from app.services.auth import ensure_fresh_token

logger = logging.getLogger(__name__)


async def _write_rsvp_log(
    db: AsyncSession,
    event: Event,
    user: User | None,
    fired_at: datetime,
    submitted_at: datetime | None,
    outcome: str,
    retry_count: int,
    error_detail: str | None = None,
) -> None:
    """Append an immutable audit row for this RSVP attempt. Caller must commit."""
    log = RsvpLog(
        event_id=event.id,
        user_id=user.id if user else None,
        spond_event_id=event.spond_event_id,
        choice=event.user_choice,
        fired_at=fired_at,
        submitted_at=submitted_at,
        outcome=outcome,
        retry_count=retry_count,
        error_detail=error_detail,
    )
    db.add(log)


async def run_executioner() -> None:
    """Entry point called by APScheduler. Never raises — logs all errors."""
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Event)
            .join(User)
            .where(
                Event.invite_time <= now,
                Event.status == STATUS_PENDING,
                Event.user_choice.in_([CHOICE_ACCEPT, CHOICE_DECLINE]),
                User.is_active == True,  # noqa: E712
            )
        )
        pending = result.scalars().all()

    if not pending:
        return

    logger.info("Executioner: %d RSVP(s) to fire.", len(pending))

    # Fire all RSVPs concurrently, one task per event
    await asyncio.gather(
        *[_process_event(event) for event in pending],
        return_exceptions=True,
    )


async def _process_event(event: Event) -> None:
    """Handle a single RSVP submission with one automatic retry on 401."""
    fired_at = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        # Atomically claim the event — only the caller that claims rowcount=1 proceeds.
        result = await db.execute(
            update(Event)
            .where(Event.id == event.id, Event.status == STATUS_PENDING)
            .values(status=STATUS_PROCESSING, updated_at=datetime.now(timezone.utc))
        )
        if result.rowcount == 0:
            return  # already claimed or processed by another worker / sniper

        db_event = await db.get(Event, event.id)
        if not db_event:
            return

        user = await db.get(User, db_event.user_id)
        if not user or not user.profile_id:
            logger.error(
                "Event %s has no resolvable user or profile_id — skipping.",
                db_event.id,
            )
            db_event.status = STATUS_FAILED
            db_event.error_message = "User not found or missing profile_id."
            await _write_rsvp_log(
                db, db_event, None, fired_at, None, OUTCOME_FAILED, 0,
                "User not found or missing profile_id.",
            )
            await db.commit()
            return

        accepted = db_event.user_choice == CHOICE_ACCEPT

        try:
            submitted_at = await _submit_rsvp(
                db,
                user,
                db_event.spond_event_id,
                accepted,
            )
            db_event.status = STATUS_PROCESSED
            db_event.error_message = None
            await _write_rsvp_log(db, db_event, user, fired_at, submitted_at, OUTCOME_SUCCESS, 0)
            logger.info(
                "RSVP %s for %r (%r) → SUCCESS",
                "ACCEPT" if accepted else "DECLINE",
                user.display_name,
                db_event.heading,
            )
        except SpondAuthError:
            logger.warning(
                "401 on RSVP for %r — forcing token refresh and retrying.",
                user.display_name,
            )
            try:
                submitted_at = await _submit_rsvp(
                    db,
                    user,
                    db_event.spond_event_id,
                    accepted,
                    force_refresh=True,
                )
                db_event.status = STATUS_PROCESSED
                db_event.error_message = None
                await _write_rsvp_log(
                    db, db_event, user, fired_at, submitted_at, OUTCOME_RETRY_SUCCESS, 1
                )
                logger.info(
                    "RSVP %s for %r (%r) → SUCCESS (after retry)",
                    "ACCEPT" if accepted else "DECLINE",
                    user.display_name,
                    db_event.heading,
                )
            except Exception as retry_exc:
                db_event.status = STATUS_FAILED
                db_event.error_message = f"Retry failed: {retry_exc}"
                await _write_rsvp_log(
                    db, db_event, user, fired_at, None, OUTCOME_FAILED, 1, str(retry_exc)
                )
                logger.error(
                    "RSVP failed for %r (%r) after retry: %s",
                    user.display_name,
                    db_event.heading,
                    retry_exc,
                )
        except SpondAPIError as exc:
            db_event.status = STATUS_FAILED
            db_event.error_message = str(exc)
            await _write_rsvp_log(
                db, db_event, user, fired_at, None, OUTCOME_FAILED, 0, str(exc)
            )
            logger.error(
                "RSVP API error for %r (%r): %s",
                user.display_name,
                db_event.heading,
                exc,
            )
        except Exception as exc:
            db_event.status = STATUS_FAILED
            db_event.error_message = f"Unexpected error: {exc}"
            await _write_rsvp_log(
                db, db_event, user, fired_at, None, OUTCOME_FAILED, 0,
                f"Unexpected error: {exc}",
            )
            logger.exception(
                "Unexpected RSVP error for %r (%r): %s",
                user.display_name,
                db_event.heading,
                exc,
            )

        await db.commit()


async def _submit_rsvp(
    db: AsyncSession,
    user: User,
    spond_event_id: str,
    accepted: bool,
    *,
    force_refresh: bool = False,
) -> datetime:
    """Obtain a fresh token, resolve recipient ID, fire the RSVP. Returns submitted_at."""
    token = await ensure_fresh_token(db, user, force=force_refresh)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
        bulk = await spond_client.get_bulk_events(http, token, [spond_event_id])
        if not bulk:
            raise SpondAPIError(f"Event {spond_event_id} not found on Spond server")

        raw_event = bulk[0]

        recipient_id = await spond_client.resolve_recipient_id(
            http, token, raw_event, user.login, user.profile_id  # type: ignore[arg-type]
        )

        logger.info(
            "RSVP recipient resolved: user=%r event=%s recipient_id=%s (profile_id=%s)",
            user.display_name, spond_event_id, recipient_id, user.profile_id,
        )

        submitted_at = datetime.now(timezone.utc)
        await spond_client.rsvp(http, token, spond_event_id, recipient_id, accepted)
        return submitted_at


# ---------------------------------------------------------------------------
# Sniper helpers — per-event DateTrigger jobs for millisecond-precision RSVPs
# ---------------------------------------------------------------------------

def _sniper_job_id(event_id: _uuid.UUID) -> str:
    return f"sniper_{event_id}"


def schedule_sniper(scheduler: AsyncIOScheduler, event: Event) -> None:
    """Schedule (or replace) a one-shot RSVP job at event.invite_time minus lead time."""
    from app.config import settings

    now = datetime.now(timezone.utc)
    if not event.invite_time or event.invite_time <= now:
        return

    fire_at = event.invite_time - timedelta(milliseconds=settings.rsvp_lead_time_ms)
    if fire_at <= now:
        fire_at = now  # already past adjusted time — fire immediately

    job_id = _sniper_job_id(event.id)
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        run_sniper,
        trigger="date",
        run_date=fire_at,
        id=job_id,
        args=[event.id],
        misfire_grace_time=30,
    )
    logger.debug("Sniper scheduled for event %s at %s (lead=%dms)", event.id, fire_at, settings.rsvp_lead_time_ms)


def cancel_sniper(scheduler: AsyncIOScheduler, event_id: _uuid.UUID) -> None:
    """Cancel a pending sniper job if it exists."""
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(_sniper_job_id(event_id))


async def run_sniper(event_id: _uuid.UUID) -> None:
    """One-shot job called by APScheduler at invite_time."""
    async with AsyncSessionLocal() as db:
        event = await db.get(Event, event_id)
    if event:
        await _process_event(event)

