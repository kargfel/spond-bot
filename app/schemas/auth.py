"""
Auth schemas for the frontend auth layer.
"""
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class FrontendUserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8)
    is_admin: bool = False
    linked_user_id: UUID | None = None


class FrontendUserResponse(BaseModel):
    id: UUID
    username: str
    is_admin: bool
    linked_user_id: UUID | None

    model_config = {"from_attributes": True}


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class FrontendUserUpdate(BaseModel):
    """Admin-only partial update schema for a FrontendUser."""
    is_admin: bool | None = None
    linked_user_id: UUID | None = None
    new_password: str | None = Field(default=None, min_length=8)
