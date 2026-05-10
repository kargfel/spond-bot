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
from datetime import datetime, timezone

import aiohttp
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import spond_client
from app.core.spond_client import SpondAPIError, SpondAuthError
from app.database import AsyncSessionLocal
from app.models.event import (
    CHOICE_ACCEPT,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_PROCESSING,
    Event,
)
from app.models.user import User
from app.services.auth import ensure_fresh_token

logger = logging.getLogger(__name__)


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
                Event.user_choice.in_([CHOICE_ACCEPT, "decline"]),
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
    async with AsyncSessionLocal() as db:
        # Re-fetch within the new session so writes are tracked
        db_event = await db.get(Event, event.id)
        if not db_event or db_event.status != STATUS_PENDING:
            return

        user = await db.get(User, db_event.user_id)
        if not user or not user.profile_id:
            logger.error(
                "Event %s has no resolvable user or profile_id — skipping.",
                db_event.id,
            )
            db_event.status = STATUS_FAILED
            db_event.error_message = "User not found or missing profile_id."
            await db.commit()
            return

        # Mark as processing to prevent duplicate execution if scheduler overlaps
        db_event.status = STATUS_PROCESSING
        await db.commit()

        accepted = db_event.user_choice == CHOICE_ACCEPT

        try:
            await _submit_rsvp(db, user, db_event.spond_event_id, accepted)
            db_event.status = STATUS_PROCESSED
            db_event.error_message = None
            logger.info(
                "RSVP %s for %r (%r) → SUCCESS",
                "ACCEPT" if accepted else "DECLINE",
                user.display_name,
                db_event.heading,
            )
        except SpondAuthError:
            # Force token refresh and retry once
            logger.warning(
                "401 on RSVP for %r — forcing token refresh and retrying.",
                user.display_name,
            )
            try:
                await _submit_rsvp(
                    db, user, db_event.spond_event_id, accepted, force_refresh=True
                )
                db_event.status = STATUS_PROCESSED
                db_event.error_message = None
                logger.info(
                    "RSVP %s for %r (%r) → SUCCESS (after retry)",
                    "ACCEPT" if accepted else "DECLINE",
                    user.display_name,
                    db_event.heading,
                )
            except Exception as retry_exc:
                db_event.status = STATUS_FAILED
                db_event.error_message = f"Retry failed: {retry_exc}"
                logger.error(
                    "RSVP failed for %r (%r) after retry: %s",
                    user.display_name,
                    db_event.heading,
                    retry_exc,
                )
        except SpondAPIError as exc:
            db_event.status = STATUS_FAILED
            db_event.error_message = str(exc)
            logger.error(
                "RSVP API error for %r (%r): %s",
                user.display_name,
                db_event.heading,
                exc,
            )
        except Exception as exc:
            db_event.status = STATUS_FAILED
            db_event.error_message = f"Unexpected error: {exc}"
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
) -> None:
    """Obtain a fresh token, resolve the correct recipient ID, and fire the RSVP request."""
    token = await ensure_fresh_token(db, user, force=force_refresh)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
        # 1. Fetch full event details (needed to find the group)
        bulk = await spond_client.get_bulk_events(http, token, [spond_event_id])
        if not bulk:
            raise SpondAPIError(f"Event {spond_event_id} not found on Spond server")

        raw_event = bulk[0]

        # 2. Resolve the correct member ID for this user in this event's group.
        #    Spond uses per-group member IDs (not the global profile ID) for RSVPs.
        recipient_id = await spond_client.resolve_recipient_id(
            http, token, raw_event, user.login, user.profile_id  # type: ignore[arg-type]
        )

        logger.info(
            "RSVP recipient resolved: user=%r event=%s recipient_id=%s (profile_id=%s)",
            user.display_name, spond_event_id, recipient_id, user.profile_id,
        )

        # 3. Submit the RSVP
        await spond_client.rsvp(http, token, spond_event_id, recipient_id, accepted)


# ---------------------------------------------------------------------------
# Sniper helpers — per-event DateTrigger jobs for millisecond-precision RSVPs
# ---------------------------------------------------------------------------

def _sniper_job_id(event_id: _uuid.UUID) -> str:
    return f"sniper_{event_id}"


def schedule_sniper(scheduler: AsyncIOScheduler, event: Event) -> None:
    """Schedule (or replace) a one-shot RSVP job at event.invite_time."""
    now = datetime.now(timezone.utc)
    if not event.invite_time or event.invite_time <= now:
        return
    job_id = _sniper_job_id(event.id)
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        run_sniper,
        trigger="date",
        run_date=event.invite_time,
        id=job_id,
        args=[event.id],
        misfire_grace_time=30,
    )
    logger.debug("Sniper scheduled for event %s at %s", event.id, event.invite_time)


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

