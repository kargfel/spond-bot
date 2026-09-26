"""
/api/v1/admin — Admin-only observability endpoints.

All endpoints require is_admin == True (enforced via AdminDep).

GET  /admin/rsvp-log        Paginated RSVP audit log
GET  /admin/stats           System health stats
POST /admin/sync            Trigger a discovery sync
"""
import contextlib
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, DbDep
from app.models.event import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    Event,
)
from app.models.rsvp_log import RsvpLog
from app.models.user import User
from app.schemas.charts import ChartsResponse, DailyRate, LatencyPoint, PerUserStats
from app.schemas.scheduler import ScheduledJob
from app.schemas.rsvp_log import RsvpLogResponse
from app.schemas.stats import AdminStatsResponse, RecentFailure

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get(
    "/rsvp-log",
    response_model=list[RsvpLogResponse],
    dependencies=[AdminDep],
    summary="RSVP audit log (admin only)",
)
async def get_rsvp_log(
    db: AsyncSession = DbDep,
    user_id: uuid.UUID | None = Query(None, description="Filter by Spond user UUID"),
    since: datetime | None = Query(None, description="Return only entries fired after this UTC timestamp"),
    limit: int = Query(100, le=500, description="Maximum rows to return"),
):
    """
    Returns RSVP attempt records in reverse-chronological order.
    Each row captures who fired the RSVP, when, the outcome, and any error.
    """
    q = select(RsvpLog).order_by(RsvpLog.fired_at.desc()).limit(limit)
    if user_id:
        q = q.where(RsvpLog.user_id == user_id)
    if since:
        q = q.where(RsvpLog.fired_at >= since)
    result = await db.execute(q)
    return result.scalars().all()


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    dependencies=[AdminDep],
    summary="System health stats (admin only)",
)
async def get_admin_stats(db: AsyncSession = DbDep):
    """
    Returns aggregated system health data:
    - User and event counts by status
    - Last discovery sync timestamp
    - Up to 10 most recent failed events
    """
    from app.workers.discovery import last_discovery_at

    active_users = (
        await db.execute(select(func.count()).where(User.is_active == True))  # noqa: E712
    ).scalar_one()

    total_events = (await db.execute(select(func.count()).select_from(Event))).scalar_one()

    events_pending = (
        await db.execute(select(func.count()).where(Event.status == STATUS_PENDING))
    ).scalar_one()

    events_processed = (
        await db.execute(select(func.count()).where(Event.status == STATUS_PROCESSED))
    ).scalar_one()

    events_failed = (
        await db.execute(select(func.count()).where(Event.status == STATUS_FAILED))
    ).scalar_one()

    failed_rows = (
        await db.execute(
            select(Event, User.display_name)
            .join(User, Event.user_id == User.id)
            .where(Event.status == STATUS_FAILED)
            .order_by(Event.updated_at.desc())
            .limit(10)
        )
    ).all()

    recent_failures = [
        RecentFailure(
            event_id=ev.id,
            user_display_name=display_name,
            heading=ev.heading,
            error_message=ev.error_message,
            updated_at=ev.updated_at,
        )
        for ev, display_name in failed_rows
    ]

    # Compute timing percentiles from the last 200 RSVP submissions
    # that have both submitted_at and the event's invite_time.
    # Delta = submitted_at - invite_time in milliseconds.
    # Uses Python-side percentile since SQLite (tests) doesn't support percentile_cont.
    from sqlalchemy import and_

    timing_rows = (
        await db.execute(
            select(
                RsvpLog.submitted_at,
                Event.invite_time,
            )
            .join(Event, RsvpLog.event_id == Event.id)
            .where(
                and_(
                    RsvpLog.submitted_at.is_not(None),
                    Event.invite_time.is_not(None),
                )
            )
            .order_by(RsvpLog.fired_at.desc())
            .limit(200)
        )
    ).all()

    rsvp_p50_ms = None
    rsvp_p95_ms = None
    rsvp_sample_count = len(timing_rows)

    if timing_rows:
        deltas_ms = sorted(
            int((row.submitted_at - row.invite_time).total_seconds() * 1000)
            for row in timing_rows
            if row.submitted_at and row.invite_time
        )
        if deltas_ms:
            def _percentile(data: list[int], p: float) -> int:
                idx = max(0, int(len(data) * p / 100) - 1)
                return data[min(idx, len(data) - 1)]

            rsvp_p50_ms = _percentile(deltas_ms, 50)
            rsvp_p95_ms = _percentile(deltas_ms, 95)
            rsvp_sample_count = len(deltas_ms)

    return AdminStatsResponse(
        active_users=active_users,
        total_events=total_events,
        events_pending=events_pending,
        events_processed=events_processed,
        events_failed=events_failed,
        last_discovery_at=last_discovery_at,
        recent_failures=recent_failures,
        rsvp_p50_ms=rsvp_p50_ms,
        rsvp_p95_ms=rsvp_p95_ms,
        rsvp_sample_count=rsvp_sample_count,
    )


@router.get(
    "/charts",
    response_model=ChartsResponse,
    dependencies=[AdminDep],
    summary="Chart data: latency scatter, daily success rate, per-user breakdown (admin only)",
)
async def get_charts(
    db: AsyncSession = DbDep,
    days: int = Query(30, le=90, description="Days of history to include"),
    user_id: uuid.UUID | None = Query(None, description="Filter to a single Spond user"),
):
    """Returns chart data for the last N days: latency scatter, daily outcomes, per-user breakdown."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (
        select(RsvpLog, User.display_name, Event.invite_time, Event.heading)
        .join(Event, RsvpLog.event_id == Event.id)
        .join(User, RsvpLog.user_id == User.id)
        .where(RsvpLog.fired_at >= since)
        .order_by(RsvpLog.fired_at.asc())
    )
    if user_id:
        q = q.where(RsvpLog.user_id == user_id)
    rows = (await db.execute(q)).all()

    scatter: list[LatencyPoint] = []
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"success": 0, "failed": 0, "retry_success": 0})
    user_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "success": 0, "latencies": []})

    for log, display_name, invite_time, heading in rows:
        if log.submitted_at and invite_time:
            submitted_naive = log.submitted_at.replace(tzinfo=None)
            invite_naive = invite_time.replace(tzinfo=None)
            latency_ms = int((submitted_naive - invite_naive).total_seconds() * 1000)
            scatter.append(LatencyPoint(
                fired_at=log.fired_at,
                latency_ms=latency_ms,
                user_name=display_name,
                heading=heading,
            ))
            user_stats[display_name]["latencies"].append(latency_ms)

        date_str = log.fired_at.strftime("%Y-%m-%d")
        outcome = log.outcome if log.outcome in ("success", "failed", "retry_success") else "failed"
        daily[date_str][outcome] += 1

        user_stats[display_name]["total"] += 1
        if log.outcome in ("success", "retry_success"):
            user_stats[display_name]["success"] += 1

    daily_rates = [
        DailyRate(
            date=date,
            success=counts["success"],
            failed=counts["failed"],
            retry_success=counts["retry_success"],
        )
        for date, counts in sorted(daily.items())
    ]

    per_user: list[PerUserStats] = []
    for uname, udata in user_stats.items():
        lats = sorted(udata["latencies"])
        n = len(lats)
        p50 = lats[max(0, int(n * 0.5) - 1)] if lats else None
        p95 = lats[max(0, int(n * 0.95) - 1)] if lats else None
        per_user.append(PerUserStats(
            user_name=uname,
            total=udata["total"],
            success=udata["success"],
            p50_ms=p50,
            p95_ms=p95,
        ))

    return ChartsResponse(
        latency_scatter=scatter,
        daily_success_rate=daily_rates,
        per_user=sorted(per_user, key=lambda u: u.total, reverse=True),
    )


@router.get(
    "/scheduler",
    response_model=list[ScheduledJob],
    dependencies=[AdminDep],
    summary="List armed sniper jobs (admin only)",
)
async def list_scheduler_jobs(db: AsyncSession = DbDep):
    """Returns all currently-scheduled sniper jobs sorted by fire time."""
    from app.workers.scheduler import get_scheduler

    scheduler = get_scheduler()
    now = datetime.now(timezone.utc)
    result: list[ScheduledJob] = []

    for job in scheduler.get_jobs():
        if not job.id.startswith("sniper_"):
            continue
        try:
            event_id = uuid.UUID(job.id[len("sniper_"):])
        except ValueError:
            continue
        fire_at = getattr(job, "next_run_time", None)
        if not fire_at:
            continue

        event = await db.get(Event, event_id)
        if not event:
            continue
        user = await db.get(User, event.user_id)

        result.append(ScheduledJob(
            job_id=job.id,
            event_id=event_id,
            heading=event.heading,
            user_name=user.display_name if user else None,
            fire_at=fire_at,
            countdown_s=max(0, int((fire_at - now).total_seconds())),
        ))

    return sorted(result, key=lambda j: j.fire_at)


@router.delete(
    "/scheduler/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[AdminDep],
    summary="Cancel an armed sniper job (admin only)",
)
async def cancel_scheduler_job(job_id: str):
    """Removes the sniper (and its warmup job) from APScheduler. Idempotent."""
    from apscheduler.jobstores.base import JobLookupError
    from app.workers.scheduler import get_scheduler

    scheduler = get_scheduler()
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(job_id)
    if job_id.startswith("sniper_"):
        warmup_id = "warmup_" + job_id[len("sniper_"):]
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job(warmup_id)


@router.post(
    "/scheduler/{job_id}/fire",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[AdminDep],
    summary="Fire a sniper job immediately (admin only)",
)
async def fire_scheduler_job(job_id: str):
    """Cancels the scheduled job and fires the sniper immediately."""
    import asyncio
    from apscheduler.jobstores.base import JobLookupError
    from app.workers.executioner import run_sniper
    from app.workers.scheduler import get_scheduler

    if not job_id.startswith("sniper_"):
        raise HTTPException(status_code=400, detail="job_id must start with 'sniper_'.")
    try:
        event_id = uuid.UUID(job_id[len("sniper_"):])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    scheduler = get_scheduler()
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(job_id)

    asyncio.create_task(run_sniper(event_id))
    return {"detail": f"Sniper for event {event_id} fired immediately."}


@router.post(
    "/sync",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[AdminDep],
    summary="Manually trigger a discovery sync (admin only)",
)
async def trigger_sync():
    """Enqueues an immediate run of the discovery worker."""
    import asyncio
    from app.workers.discovery import run_discovery
    asyncio.create_task(run_discovery())
    return {"detail": "Discovery sync triggered."}
