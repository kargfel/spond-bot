import uuid
from datetime import datetime

from pydantic import BaseModel


class RecentFailure(BaseModel):
    event_id: uuid.UUID
    user_display_name: str
    heading: str | None
    error_message: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminStatsResponse(BaseModel):
    active_users: int
    total_events: int
    events_pending: int
    events_processed: int
    events_failed: int
    last_discovery_at: datetime | None
    recent_failures: list[RecentFailure]
