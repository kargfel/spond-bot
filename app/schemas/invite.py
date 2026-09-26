import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InviteCreate(BaseModel):
    note: str | None = Field(None, max_length=100, description="Label for the admin, e.g. the member's name")
    days_valid: int = Field(7, ge=1, le=30)


class InviteResponse(BaseModel):
    id: uuid.UUID
    note: str | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    status: Literal["pending", "used", "expired"]


class InviteCreated(InviteResponse):
    token: str = Field(..., description="Shown once. Build the link as /join#<token>.")


class InviteCheckRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)


class InviteCheckResponse(BaseModel):
    valid: bool
    reason: Literal["unknown", "used", "expired"] | None
    note: str | None
    expires_at: datetime | None


class InviteAccept(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8)
    spond_login: str = Field(..., min_length=1, max_length=255)
    spond_password: str = Field(..., min_length=1)
    display_name: str | None = Field(None, min_length=1, max_length=255)
