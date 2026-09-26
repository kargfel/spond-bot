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


class SpondPasswordUpdate(BaseModel):
    """A new Spond password for an existing account (after changing it in Spond)."""
    password: str = Field(..., min_length=1)


class SpondConnect(BaseModel):
    """A signed-in login connecting its own Spond account."""
    login: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)
    display_name: str | None = Field(None, min_length=1, max_length=255)
