"""
/api/v1/events — Event listing and RSVP decision endpoints.

All endpoints require a valid JWT (CurrentUser).
Admins can see and modify all events.
Regular users can only see/modify events belonging to their linked Spond user.

GET    /events                 List events (filterable)
GET    /events/{id}            Get a single event
PATCH  /events/{id}            Set user_choice (accept/decline/manual)
GET    /health                 Health check (public)
"""
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbDep
from app.models.event import CHOICE_ACCEPT, CHOICE_MANUAL, STATUS_PENDING, Event
from app.schemas.event import EventDecisionUpdate, EventResponse
from app.services import audit
from app.workers.executioner import cancel_sniper, schedule_sniper
from app.workers.scheduler import get_scheduler

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Events"])


def _assert_event_access(event: Event, current_user: dict) -> None:
    """Raise 403 if a non-admin user tries to touch another user's event."""
    if current_user.get("is_admin"):
        return
    linked = current_user.get("linked_user_id")
    if not linked or str(event.user_id) != linked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this event.",
        )


@router.get(
    "/events",
    response_model=list[EventResponse],
    summary="List events",
)
async def list_events(
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
    user_id: uuid.UUID | None = Query(None, description="Filter by user UUID (admin only)"),
    status_filter: str | None = Query(None, alias="status"),
    choice: str | None = Query(None),
    all: bool = Query(False, description="Fetch all users' events (admin only)"),
):
    """
    Returns events visible to the caller.

    Admins can optionally pass `user_id` to filter by a specific Spond user,
    or pass `all=true` to fetch all users' events. For safety in dashboard views,
    if an admin doesn't explicitly pass `all=true` or `user_id`, they only see their own events.
    Non-admin users always get only their own events regardless of `user_id`.
    """
    q = select(Event)

    # Restrict to caller's own events unless caller is admin AND explicitly asks for 'all' or a specific 'user_id'
    if current_user.get("is_admin") and (all or user_id):
        if user_id:
            q = q.where(Event.user_id == user_id)
    else:
        linked = current_user.get("linked_user_id")
        if not linked:
            return []  # No linked Spond user → no events
        q = q.where(Event.user_id == uuid.UUID(linked))

    if status_filter:
        q = q.where(Event.status == status_filter)
    if choice:
        q = q.where(Event.user_choice == choice)
    q = q.order_by(Event.invite_time.asc().nullslast())

    result = await db.execute(q)
    return result.scalars().all()


@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    summary="Get a single event",
)
async def get_event(
    event_id: uuid.UUID,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")
    _assert_event_access(event, current_user)
    return event


@router.patch(
    "/events/{event_id}",
    response_model=EventResponse,
    summary="Set RSVP decision for an event",
)
async def set_decision(
    event_id: uuid.UUID,
    payload: EventDecisionUpdate,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    """
    Set whether the user wants to accept, decline, or leave this event as manual.

    - Setting choice to `manual` effectively disables automatic RSVP for this event.
    - If a previously `failed` event is updated, its status resets to `pending`
      so the executioner will retry it.
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")
    _assert_event_access(event, current_user)

    previous = event.user_choice
    event.user_choice = payload.user_choice

    if event.status == "failed" and payload.user_choice != CHOICE_MANUAL:
        event.status = STATUS_PENDING
        event.error_message = None

    await db.commit()
    await db.refresh(event)

    scheduler = get_scheduler()
    if event.user_choice in (CHOICE_ACCEPT, "decline") and event.status == STATUS_PENDING:
        schedule_sniper(scheduler, event)
    else:
        cancel_sniper(scheduler, event.id)

    audit.record(
        "event.choice_set", target_type="event", target_id=event.id, target_label=event.heading,
        details={"from": previous, "to": payload.user_choice, "owner_user_id": event.user_id, "status": event.status},
    )
    logger.info(
        "Decision set to %r for event %s (%r) by %r",
        payload.user_choice,
        event_id,
        event.heading,
        current_user.get("username"),
    )
    return event


@router.get("/health", summary="Health check", include_in_schema=False)
async def health(db: AsyncSession = DbDep):
    """
    Returns 200 when the database is reachable and the scheduler is running,
    503 otherwise. Docker's HEALTHCHECK relies on the status code, so a broken
    dependency must never produce a 200.
    """
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        db_status = "error"

    scheduler_status = "running" if get_scheduler().running else "stopped"
    healthy = db_status == "ok" and scheduler_status == "running"
    body = {"status": "ok" if healthy else "error", "db": db_status, "scheduler": scheduler_status}
    if not healthy:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)
    return body
