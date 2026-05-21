import uuid
from datetime import datetime

from pydantic import BaseModel


class RsvpLogResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None
    user_id: uuid.UUID | None
    spond_event_id: str
    choice: str
    fired_at: datetime
    submitted_at: datetime | None
    outcome: str
    retry_count: int
    error_detail: str | None

    model_config = {"from_attributes": True}
