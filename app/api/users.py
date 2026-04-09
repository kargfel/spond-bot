"""
/api/v1/users — User management endpoints.

Admins can list, create, update, and delete Spond user accounts.
Regular users can only read and update their own linked Spond user profile.

POST   /users           Register a new Spond user (admin only)
GET    /users           List all users (admin only)
GET    /users/{id}      Get a single user (admin or own)
PATCH  /users/{id}      Update display_name or is_active (admin or own)
DELETE /users/{id}      Remove user and cascade-delete events (admin only)
"""
import logging
import uuid

import aiohttp
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, CurrentUser, DbDep
from app.core import spond_client
from app.core.security import encrypt
from app.core.spond_client import SpondAuthError
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


def _assert_own_or_admin(user_id: uuid.UUID, current_user: dict) -> None:
    if current_user.get("is_admin"):
        return
    linked = current_user.get("linked_user_id")
    if not linked or str(user_id) != linked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own profile.",
        )


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminDep],
    summary="Register a Spond user (admin only)",
)
async def create_user(payload: UserCreate, db: AsyncSession = DbDep):
    """
    Register a new Spond user. Validates credentials live against the Spond API.
    Credentials are stored encrypted; plaintext is never persisted.
    Only admins can do this.
    """
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
    logger.info("Registered Spond user %r (profile_id=%s)", user.display_name, user.profile_id)
    return user


@router.get(
    "",
    response_model=list[UserResponse],
    dependencies=[AdminDep],
    summary="List all Spond users (admin only)",
)
async def list_users(db: AsyncSession = DbDep):
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get a Spond user (admin or own profile)",
)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    _assert_own_or_admin(user_id, current_user)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update display name or active status (admin or own profile)",
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    _assert_own_or_admin(user_id, current_user)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if payload.display_name is not None:
        user.display_name = payload.display_name
    # Non-admins cannot deactivate themselves
    if payload.is_active is not None and current_user.get("is_admin"):
        user.is_active = payload.is_active

    await db.commit()
    await db.refresh(user)
    return user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[AdminDep],
    summary="Delete a Spond user and all their events (admin only)",
)
async def delete_user(user_id: uuid.UUID, db: AsyncSession = DbDep):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    await db.delete(user)
    await db.commit()
