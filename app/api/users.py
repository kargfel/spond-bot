"""
/api/v1/spond-accounts — Spond account management endpoints.

Admins can list, create, update, and delete Spond user accounts.
Regular users can only read and update their own linked Spond user profile.

POST   /spond-accounts           Register a new Spond user (admin only)
POST   /spond-accounts/me        Connect a Spond account to your own login (any signed-in login without one)
GET    /spond-accounts           List all users (admin only)
GET    /spond-accounts/{id}      Get a single user (admin or own)
PATCH  /spond-accounts/{id}      Update display_name or is_active (admin or own)
DELETE /spond-accounts/{id}      Remove user and cascade-delete events (admin only)
"""
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, CurrentUser, DbDep
from app.core.rate_limit import limiter
from app.core.session import set_session_cookie
from app.models.frontend_user import FrontendUser
from app.models.user import User
from app.schemas.user import SpondConnect, UserCreate, UserResponse, UserUpdate
from app.services.spond_accounts import connect_spond_account, ensure_login_available

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/spond-accounts", tags=["Spond Accounts"])


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
    await ensure_login_available(db, payload.login)
    user = await connect_spond_account(payload.login, payload.password, payload.display_name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Registered Spond user %r (profile_id=%s)", user.display_name, user.profile_id)
    return user


@router.post(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a Spond account to your own login",
)
@limiter.limit("5/minute")
async def connect_own_account(
    request: Request,
    response: Response,
    payload: SpondConnect,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    """
    For a signed-in login that has no Spond account yet. Verifies the credentials
    with Spond, links the new account, and re-issues the session cookie so the
    new link takes effect immediately. The database, not the cookie, decides
    whether the login is already linked.
    """
    login = await db.get(FrontendUser, uuid.UUID(current_user["sub"]))
    if not login:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login not found.")
    if login.linked_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your login is already linked to a Spond account. Ask your admin to change it.",
        )
    await ensure_login_available(db, payload.login)

    user = await connect_spond_account(payload.login, payload.password, payload.display_name)
    db.add(user)
    await db.flush()
    login.linked_user_id = user.id
    await db.commit()
    await db.refresh(user)
    await db.refresh(login)

    set_session_cookie(response, login)
    logger.info("Login %r connected Spond account %r.", login.username, user.login)
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
