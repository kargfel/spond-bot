from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
    login: str = Field(
        ...,
        description="Spond login: email address or phone number (e.g. +4917...)",
    )
    password: str = Field(..., min_length=1)


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: UUID
    display_name: str
    login: str
    profile_id: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
