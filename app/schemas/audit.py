import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    actor_username: str | None
    actor_is_admin: bool | None
    action: str
    category: str
    outcome: str
    target_type: str | None
    target_id: str | None
    target_label: str | None
    details: dict | None
    ip: str | None
    user_agent: str | None
    method: str | None
    path: str | None
    status_code: int | None
    request_id: str | None

    model_config = {"from_attributes": True}


class AuditLogPage(BaseModel):
    items: list[AuditLogEntry]
    # Pass back as ?cursor= for the next (older) page; None on the last page
    next_cursor: str | None
