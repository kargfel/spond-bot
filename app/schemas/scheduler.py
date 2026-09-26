from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ScheduledJob(BaseModel):
    job_id: str
    event_id: UUID
    heading: str | None
    user_name: str | None
    fire_at: datetime
    countdown_s: int
