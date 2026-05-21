"""
/api/v1/admin — Admin-only observability endpoints.

All endpoints require is_admin == True (enforced via AdminDep).

GET /admin/rsvp-log        Paginated RSVP audit log
GET /admin/stats           System health stats
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Query
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
