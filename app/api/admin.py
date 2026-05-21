"""
/api/v1/admin — Admin-only observability endpoints.

All endpoints require is_admin == True (enforced via AdminDep).

GET /admin/rsvp-log        Paginated RSVP audit log
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, DbDep
from app.models.rsvp_log import RsvpLog
from app.schemas.rsvp_log import RsvpLogResponse

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
