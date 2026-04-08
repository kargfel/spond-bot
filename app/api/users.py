"""
/api/v1/users — User management endpoints.

POST   /users           Register a new Spond user (validates creds live)
GET    /users           List all users
GET    /users/{id}      Get a single user
PATCH  /users/{id}      Update display_name or is_active
DELETE /users/{id}      Remove user and cascade-delete their events
"""
import logging
import uuid

import aiohttp
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthDep, DbDep
from app.core import spond_client
from app.core.security import encrypt
from app.core.spond_client import SpondAuthError
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AuthDep],
    summary="Register a Spond user",
)
async def create_user(payload: UserCreate, db: AsyncSession = DbDep):
    """
    Register a new user by providing their Spond credentials.

    The API immediately validates the credentials against Spond and fetches
    the user's `profile_id`, which is required for RSVP submissions.
    Credentials are stored encrypted; plaintext is never persisted.
    """
    # Reject duplicate logins before hitting the Spond API
    existing = await db.execute(select(User).where(User.login == payload.login))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with login {payload.login!r} already exists.",
        )

    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
            token, acquired_at = await spond_client.login(
                http, payload.login, payload.password
            )
            profile_id = await spond_client.get_profile_id(http, token)
    except SpondAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Spond authentication failed: {exc}",
        )
    except Exception as exc:
        logger.error("Unexpected error registering user %r: %s", payload.login, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach the Spond API. Try again later.",
        )

    user = User(
        id=uuid.uuid4(),
        display_name=payload.display_name,
        login=payload.login,
        encrypted_password=encrypt(payload.password),
        encrypted_access_token=encrypt(token),
        token_acquired_at=acquired_at,
        profile_id=profile_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(
        "Registered user %r (profile_id=%s)", user.display_name, user.profile_id
    )
    return user


@router.get(
    "",
    response_model=list[UserResponse],
    dependencies=[AuthDep],
    summary="List all users",
)
async def list_users(db: AsyncSession = DbDep):
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[AuthDep],
    summary="Get a single user",
)
async def get_user(user_id: uuid.UUID, db: AsyncSession = DbDep):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[AuthDep],
    summary="Update display name or active status",
)
async def update_user(
    user_id: uuid.UUID, payload: UserUpdate, db: AsyncSession = DbDep
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.is_active is not None:
        user.is_active = payload.is_active

    await db.commit()
    await db.refresh(user)
    return user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[AuthDep],
    summary="Delete a user and all their events",
)
async def delete_user(user_id: uuid.UUID, db: AsyncSession = DbDep):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    await db.delete(user)
    await db.commit()
