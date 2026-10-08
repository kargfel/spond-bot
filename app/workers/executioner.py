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
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import aiohttp
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import spond_client
from app.core.event_bus import bus
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
from app.services import audit, push
from app.services.auth import ensure_fresh_token

logger = logging.getLogger(__name__)

# Retry ladder (seconds) between attempts. The first rungs are tight so a request
# rejected a few ms early, or a blip, costs tens of milliseconds, not a missed spot.
_RETRY_LADDER_S = (0.05, 0.1, 0.25, 0.5, 1.0, 1.0, 2.0)
# Transient trouble (5xx, 429, timeouts, connection errors) is retried for this long after firing.
_RETRY_DEADLINE_S = 20.0
# Other 4xx answers (e.g. "not open yet" when we fire slightly early) get only these few quick
# retries; a genuine refusal such as "member not found" must not be hammered.
_REJECTED_RETRIES = 3
# Per-attempt cap so a hung connection cannot swallow the retry budget.
_ATTEMPT_TIMEOUT = aiohttp.ClientTimeout(total=5)


async def _write_rsvp_log(
    db: AsyncSession,
    event: Event,
    user: User | None,
    fired_at: datetime,
    submitted_at: datetime | None,
    outcome: str,
    retry_count: int,
    error_detail: str | None = None,
) -> RsvpLog:
    """Append an immutable row for this RSVP attempt (stats, charts). Caller must commit."""
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
    return log


def _latency_ms(submitted_at: datetime | None, invite_time: datetime | None) -> int | None:
    """Milliseconds from registration opening to the answer reaching Spond (naive times count as UTC)."""
    if not submitted_at or not invite_time:
        return None
    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return round((aware(submitted_at) - aware(invite_time)).total_seconds() * 1000)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if not later or not earlier:
        return None
    return round((_aware(later) - _aware(earlier)).total_seconds() * 1000)


def _phase_timings(rsvp_log: RsvpLog, db_event: Event, timings: dict | None) -> dict:
    """Where the time went, relative to registration opening (all ms, may be negative).

    fire_ms      job started (negative = early)
    prep_ms      job start -> first request leaves (DB, token, recipient lookup)
    request_ms   duration of the last HTTP call (success or failure)
    response_ms  answer confirmed by Spond (success only)
    attempts     requests made
    """
    if not timings or not timings.get("attempts"):
        return {}  # no request ever left
    out: dict = {}
    fire = _ms_between(rsvp_log.fired_at, db_event.invite_time)
    if fire is not None:
        out["fire_ms"] = fire
    prep = _ms_between(timings.get("first_sent_at"), rsvp_log.fired_at)
    if prep is not None:
        out["prep_ms"] = prep
    if timings.get("request_ms") is not None:
        out["request_ms"] = timings["request_ms"]
    resp = _ms_between(timings.get("done_at"), db_event.invite_time)
    if resp is not None:
        out["response_ms"] = resp
    out["attempts"] = timings["attempts"]
    return out


def _retry_delay(exc: Exception, retries: int, elapsed: float) -> float | None:
    """Seconds to wait before the next attempt, or None if this error is final."""
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
        transient = True
    elif isinstance(exc, SpondAPIError) and exc.status is not None:
        transient = exc.status in (408, 425, 429) or exc.status >= 500
    else:
        return None
    if transient:
        if elapsed >= _RETRY_DEADLINE_S:
            return None
    elif retries >= _REJECTED_RETRIES:
        return None
    return _RETRY_LADDER_S[min(retries, len(_RETRY_LADDER_S) - 1)]


async def _submit_with_retries(
    db: AsyncSession, user: User, db_event: Event, accepted: bool, timings: dict
) -> datetime:
    """Call _submit_rsvp until it works or the error is final. Fills timings['retries'].

    A 401 re-logs in and retries immediately (once). Transient failures follow the retry
    ladder; the recipient ID resolved by an earlier attempt is reused, never looked up again.
    """
    started = time.monotonic()
    force_refresh = False
    auth_refreshed = False
    while True:
        try:
            return await _submit_rsvp(
                db, user, db_event.spond_event_id, accepted,
                force_refresh=force_refresh,
                resolved_recipient_id=timings.get("recipient_id") or db_event.resolved_recipient_id,
                timings=timings,
            )
        except SpondAuthError:
            if auth_refreshed:
                raise
            logger.warning("401 on RSVP for %r — forcing token refresh and retrying.", user.display_name)
            auth_refreshed = force_refresh = True
            delay = 0.0
        except Exception as exc:
            delay = _retry_delay(exc, timings["retries"], time.monotonic() - started)
            if delay is None:
                raise
            logger.warning(
                "RSVP attempt %d for %r failed (%s) — retrying in %.0f ms.",
                timings["retries"] + 1, user.display_name, exc or type(exc).__name__, delay * 1000,
            )
            force_refresh = False
        timings["retries"] += 1
        if delay:
            await asyncio.sleep(delay)


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
    rsvp_log: RsvpLog | None = None

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
            rsvp_log = await _write_rsvp_log(
                db, db_event, None, fired_at, None, OUTCOME_FAILED, 0,
                "User not found or missing profile_id.",
            )
            await db.commit()
            await bus.publish_admin("rsvp_fired", {
                "event_id": str(db_event.id),
                "user_id": str(db_event.user_id),
                "heading": db_event.heading,
                "choice": db_event.user_choice,
                "outcome": "failed",
                "latency_ms": None,
            })
            await _notify_member(db_event, "failed", rsvp_log, user)
            return

        accepted = db_event.user_choice == CHOICE_ACCEPT
        timings: dict = {"retries": 0}
        action = "ACCEPT" if accepted else "DECLINE"

        try:
            submitted_at = await _submit_with_retries(db, user, db_event, accepted, timings)
            retries = timings["retries"]
            db_event.status = STATUS_PROCESSED
            db_event.error_message = None
            rsvp_log = await _write_rsvp_log(
                db, db_event, user, fired_at, submitted_at,
                OUTCOME_RETRY_SUCCESS if retries else OUTCOME_SUCCESS, retries,
            )
            logger.info(
                "RSVP %s for %r (%r) → SUCCESS%s",
                action, user.display_name, db_event.heading,
                f" (after {retries} retr{'y' if retries == 1 else 'ies'})" if retries else "",
            )
        except Exception as exc:
            retries = timings["retries"]
            if isinstance(exc, SpondAPIError):
                message = str(exc)
            elif isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
                message = f"Network error: {type(exc).__name__}: {exc}"
            else:
                message = f"Unexpected error: {exc}"
            if retries and isinstance(exc, (SpondAuthError, SpondAPIError)):
                message = f"Retry failed: {exc}"
            db_event.status = STATUS_FAILED
            db_event.error_message = message
            rsvp_log = await _write_rsvp_log(
                db, db_event, user, fired_at, None, OUTCOME_FAILED, retries, message
            )
            log = logger.error if isinstance(exc, (SpondAuthError, SpondAPIError, aiohttp.ClientError, asyncio.TimeoutError)) else logger.exception
            log("RSVP %s failed for %r (%r) after %d retries: %s",
                action, user.display_name, db_event.heading, retries, message)

        await db.commit()

        if db_event.status == STATUS_PROCESSED:
            await bus.publish_admin("rsvp_fired", {
                "event_id": str(db_event.id),
                "user_id": str(db_event.user_id),
                "heading": db_event.heading,
                "choice": db_event.user_choice,
                "outcome": "success",
                "latency_ms": None,
            })
            await _notify_member(db_event, "success", rsvp_log, user, timings)
        elif db_event.status == STATUS_FAILED:
            await bus.publish_admin("rsvp_fired", {
                "event_id": str(db_event.id),
                "user_id": str(db_event.user_id),
                "heading": db_event.heading,
                "choice": db_event.user_choice,
                "outcome": "failed",
                "latency_ms": None,
            })
            await _notify_member(db_event, "failed", rsvp_log, user, timings)


async def _notify_member(
    db_event: Event,
    outcome: str,
    rsvp_log: RsvpLog | None = None,
    user: User | None = None,
    timings: dict | None = None,
) -> None:
    """Tell the member their answer went out (or failed): live stream, Web Push, and the audit trail."""
    await bus.publish_user(str(db_event.user_id), "rsvp_fired", {
        "heading": db_event.heading,
        "choice": db_event.user_choice,
        "outcome": outcome,
    })
    push.dispatch_rsvp_notification(
        db_event.user_id, db_event.id, db_event.heading, db_event.user_choice, outcome
    )
    ok = outcome == "success"
    details = {
        "choice": db_event.user_choice,
        "member": user.display_name if user else None,
        "spond_user_id": db_event.user_id,
        "spond_event_id": db_event.spond_event_id,
    }
    if rsvp_log:
        details["latency_ms"] = _latency_ms(rsvp_log.submitted_at, db_event.invite_time)
        if rsvp_log.retry_count:
            details["retries"] = rsvp_log.retry_count
    details.update(_phase_timings(rsvp_log, db_event, timings))
    if not ok:
        details["error"] = db_event.error_message
    await audit.record_system(
        "rsvp.sent" if ok else "rsvp.failed",
        outcome="success" if ok else "failed",
        target_type="event", target_id=db_event.id, target_label=db_event.heading,
        details=details,
    )


async def _submit_rsvp(
    db: AsyncSession,
    user: User,
    spond_event_id: str,
    accepted: bool,
    *,
    force_refresh: bool = False,
    resolved_recipient_id: str | None = None,
    timings: dict | None = None,
) -> datetime:
    """Obtain a fresh token, resolve recipient ID, fire the RSVP. Returns submitted_at.

    If resolved_recipient_id is known (cached by warmup or an earlier attempt), skip the
    get_bulk_events and resolve_recipient_id API calls entirely. force_refresh only forces a
    re-login; the recipient ID does not depend on the token.

    timings, if given, is filled with: recipient_id, attempts, first_sent_at, request_ms, done_at.
    """
    timings = timings if timings is not None else {}
    token = await ensure_fresh_token(db, user, force=force_refresh)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(), timeout=_ATTEMPT_TIMEOUT) as http:
        if resolved_recipient_id:
            recipient_id = resolved_recipient_id
        else:
            bulk = await spond_client.get_bulk_events(http, token, [spond_event_id])
            if not bulk:
                raise SpondAPIError(f"Event {spond_event_id} not found on Spond server")
            raw_event = bulk[0]
            recipient_id = await spond_client.resolve_recipient_id(
                http, token, raw_event, user.login, user.profile_id  # type: ignore[arg-type]
            )
        timings["recipient_id"] = recipient_id

        logger.info(
            "RSVP recipient resolved: user=%r event=%s recipient_id=%s (profile_id=%s)",
            user.display_name, spond_event_id, recipient_id, user.profile_id,
        )

        submitted_at = datetime.now(timezone.utc)
        timings["attempts"] = timings.get("attempts", 0) + 1
        timings.setdefault("first_sent_at", submitted_at)
        t0 = time.perf_counter()
        try:
            await spond_client.rsvp(http, token, spond_event_id, recipient_id, accepted)
        finally:
            timings["request_ms"] = round((time.perf_counter() - t0) * 1000)
        timings["done_at"] = datetime.now(timezone.utc)
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
    invite = event.invite_time
    if not invite:
        return
    # Normalize: if invite_time is naive, compare against naive UTC
    if invite.tzinfo is None:
        now_cmp = now.replace(tzinfo=None)
    else:
        now_cmp = now
    if invite <= now_cmp:
        return

    fire_at = invite - timedelta(milliseconds=settings.rsvp_lead_time_ms)
    if fire_at <= now_cmp:
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
    asyncio.create_task(bus.publish_admin("scheduler_changed", {"action": "scheduled", "event_id": str(event.id)}))
    schedule_warmup(scheduler, event)


def cancel_sniper(scheduler: AsyncIOScheduler, event_id: _uuid.UUID) -> None:
    """Cancel a pending sniper job if it exists."""
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(_sniper_job_id(event_id))
    cancel_warmup(scheduler, event_id)
    asyncio.create_task(bus.publish_admin("scheduler_changed", {"action": "cancelled", "event_id": str(event_id)}))


async def run_sniper(event_id: _uuid.UUID) -> None:
    """One-shot job called by APScheduler at invite_time."""
    async with AsyncSessionLocal() as db:
        event = await db.get(Event, event_id)
    if event:
        await _process_event(event)


# ---------------------------------------------------------------------------
# Warmup helpers — pre-resolve recipient_id before invite_time
# ---------------------------------------------------------------------------

def _warmup_job_id(event_id: _uuid.UUID) -> str:
    return f"warmup_{event_id}"


def schedule_warmup(scheduler: AsyncIOScheduler, event: Event) -> None:
    """Schedule a pre-fetch job 10 s before the sniper fires to cache recipient_id."""
    from app.config import settings

    now = datetime.now(timezone.utc)
    invite = event.invite_time
    if not invite:
        return

    fire_at = (
        invite
        - timedelta(milliseconds=settings.rsvp_lead_time_ms)
        - timedelta(seconds=10)
    )
    # Normalize: if fire_at is naive, compare against naive UTC
    now_cmp = now.replace(tzinfo=None) if fire_at.tzinfo is None else now
    if fire_at <= now_cmp:
        return  # too close to fire time — skip warmup, sniper falls back

    job_id = _warmup_job_id(event.id)
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        run_warmup,
        trigger="date",
        run_date=fire_at,
        id=job_id,
        args=[event.id],
        misfire_grace_time=10,
    )
    logger.debug("Warmup scheduled for event %s at %s", event.id, fire_at)


def cancel_warmup(scheduler: AsyncIOScheduler, event_id: _uuid.UUID) -> None:
    """Cancel a pending warmup job if it exists."""
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(_warmup_job_id(event_id))


async def run_warmup(event_id: _uuid.UUID) -> None:
    """Pre-resolve recipient_id and cache it in events.resolved_recipient_id.

    Runs ~10 s before invite_time. Failures are logged as warnings; the sniper
    will fall back to full resolution if this field is still None at fire time.
    """
    async with AsyncSessionLocal() as db:
        event = await db.get(Event, event_id)
        if not event or event.status != STATUS_PENDING:
            return
        if event.user_choice not in (CHOICE_ACCEPT, CHOICE_DECLINE):
            return

        user = await db.get(User, event.user_id)
        if not user or not user.profile_id:
            return

        try:
            token = await ensure_fresh_token(db, user)
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
                bulk = await spond_client.get_bulk_events(http, token, [event.spond_event_id])
                if not bulk:
                    logger.warning(
                        "Warmup: event %s not found on Spond — sniper will resolve at fire time",
                        event_id,
                    )
                    return
                raw_event = bulk[0]
                recipient_id = await spond_client.resolve_recipient_id(
                    http, token, raw_event, user.login, user.profile_id  # type: ignore[arg-type]
                )

            event.resolved_recipient_id = recipient_id
            await db.commit()
            logger.debug(
                "Warmup cached recipient_id=%s for event %s", recipient_id, event_id
            )
        except Exception as exc:
            logger.warning(
                "Warmup failed for event %s: %s — sniper will fall back to full resolution",
                event_id,
                exc,
            )

