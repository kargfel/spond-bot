"""
SSE stream endpoints.

GET /admin/stream  — admin-only; emits rsvp_fired, discovery_completed, scheduler_changed
GET /user/stream   — authenticated user; emits rsvp_fired for their linked Spond user
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import AdminDep, CurrentUser
from app.core.event_bus import BusEvent, bus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Stream"])

_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
_HEARTBEAT_INTERVAL = 30


async def _stream(q: "asyncio.Queue[BusEvent]", on_exit) -> AsyncGenerator[str, None]:
    try:
        yield "event: heartbeat\ndata: {}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
            except asyncio.TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
    finally:
        on_exit()


@router.get("/admin/stream", dependencies=[AdminDep], include_in_schema=True)
async def admin_stream():
    q = bus.subscribe_admin()
    return StreamingResponse(
        _stream(q, lambda: bus.unsubscribe_admin(q)),
        media_type="text/event-stream",
        headers=_HEADERS,
    )


@router.get("/user/stream")
async def user_stream(current_user: dict = CurrentUser):
    user_id = current_user.get("linked_user_id") or current_user.get("sub")
    q = bus.subscribe_user(str(user_id))
    return StreamingResponse(
        _stream(q, lambda: bus.unsubscribe_user(str(user_id), q)),
        media_type="text/event-stream",
        headers=_HEADERS,
    )
