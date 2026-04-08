"""
/api/v1/events — Event listing and RSVP decision endpoints.

GET    /events                 List events (filterable)
GET    /events/{id}            Get a single event
PATCH  /events/{id}/decision   Set user_choice (accept/decline/manual)
POST   /sync                   Manually trigger discovery sync
GET    /health                 Health check
"""
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthDep, DbDep
from app.models.event import CHOICE_MANUAL, STATUS_PENDING, Event
from app.schemas.event import EventDecisionUpdate, EventResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Events"])


@router.get(
    "/events",
    response_model=list[EventResponse],
    dependencies=[AuthDep],
    summary="List events",
)
async def list_events(
    db: AsyncSession = DbDep,
    user_id: uuid.UUID | None = Query(None, description="Filter by user UUID"),
    status_filter: str | None = Query(None, alias="status", description="e.g. pending, processed, failed"),
    choice: str | None = Query(None, description="e.g. accept, decline, manual"),
):
    """
    Returns all known events, optionally filtered by user, status, or user_choice.
    Results are ordered by invite_time ascending (soonest first).
    """
    q = select(Event)
    if user_id:
        q = q.where(Event.user_id == user_id)
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
    dependencies=[AuthDep],
    summary="Get a single event",
)
async def get_event(event_id: uuid.UUID, db: AsyncSession = DbDep):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")
    return event


@router.patch(
    "/events/{event_id}/decision",
    response_model=EventResponse,
    dependencies=[AuthDep],
    summary="Set RSVP decision for an event",
)
async def set_decision(
    event_id: uuid.UUID,
    payload: EventDecisionUpdate,
    db: AsyncSession = DbDep,
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

    event.user_choice = payload.user_choice

    # Reset a failed event so the executioner will try again
    if event.status == "failed" and payload.user_choice != CHOICE_MANUAL:
        event.status = STATUS_PENDING
        event.error_message = None

    await db.commit()
    await db.refresh(event)
    logger.info(
        "Decision set to %r for event %s (%r)",
        payload.user_choice,
        event_id,
        event.heading,
    )
    return event


@router.post(
    "/sync",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[AuthDep],
    summary="Manually trigger a discovery sync",
)
async def trigger_sync():
    """
    Enqueues an immediate run of the discovery worker.

    The worker runs asynchronously; this endpoint returns immediately.
    Check /events after a few seconds to see newly discovered events.
    """
    from app.workers.discovery import run_discovery

    import asyncio
    asyncio.create_task(run_discovery())
    return {"detail": "Discovery sync triggered."}


@router.get(
    "/health",
    summary="Health check",
    include_in_schema=False,
)
async def health(db: AsyncSession = DbDep):
    """Returns 200 if the app and database are reachable."""
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        db_status = "error"

    return {"status": "ok", "db": db_status}
