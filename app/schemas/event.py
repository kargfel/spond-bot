from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class EventDecisionUpdate(BaseModel):
    user_choice: Literal["accept", "decline", "manual"]


class EventResponse(BaseModel):
    id: UUID
    spond_event_id: str
    user_id: UUID
    heading: str | None
    start_timestamp: datetime | None
    invite_time: datetime | None
    rsvp_date: datetime | None
    user_choice: str
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
