"""
/auth — Frontend authentication endpoints.

POST /auth/login                Login with username + password → sets HttpOnly session cookie
POST /auth/logout               Clear the session cookie
GET  /auth/me                   Return current user info (requires cookie)
POST /auth/users                Create a new frontend user (admin only)
GET  /auth/users                List all frontend users (admin only)
DELETE /auth/users/{id}         Delete a frontend user (admin only)
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, CurrentUser, DbDep
from app.config import settings
from app.core.jwt import ACCESS_TOKEN_TTL, create_access_token
from app.models.frontend_user import FrontendUser
from app.schemas.auth import (
    FrontendUserCreate,
    FrontendUserResponse,
    FrontendUserUpdate,
    LoginRequest,
    PasswordChange,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_limiter = Limiter(key_func=get_remote_address)

# Cookie name and settings
_COOKIE_NAME = "sb_session"
_COOKIE_MAX_AGE = int(ACCESS_TOKEN_TTL.total_seconds())
_IS_SECURE = settings.site_domain != "localhost"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain[:72], hashed)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain[:72])


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    summary="Login and set a secure session cookie",
    status_code=status.HTTP_204_NO_CONTENT,
)
@_limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = DbDep,
):
    """
    Validate username + password.

    On success, sets an HttpOnly, Secure, SameSite=Strict cookie named
    ``sb_session`` containing a signed JWT. The cookie is never readable by
    JavaScript. Passwords are verified against the stored bcrypt hash.

    Rate-limited to 5 attempts per minute per IP address.
    """
    result = await db.execute(
        select(FrontendUser).where(FrontendUser.username == payload.username)
    )
    user = result.scalar_one_or_none()

    # Always run bcrypt to prevent user enumeration via timing attacks.
    # If the user doesn't exist we check against a dummy hash and then reject.
    reference_hash = user.hashed_password if user else hash_password("dummy_timing_mitigation")
    password_ok = verify_password(payload.password, reference_hash)

    if not user or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )

    token = create_access_token(
        {
            "sub": str(user.id),
            "username": user.username,
            "is_admin": user.is_admin,
            "linked_user_id": str(user.linked_user_id) if user.linked_user_id else None,
        }
    )

    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_IS_SECURE,
        samesite="strict",
        path="/",
    )
    logger.info("Frontend user %r logged in.", user.username)


@router.post(
    "/logout",
    summary="Clear the session cookie",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(response: Response):
    """Delete the session cookie, effectively logging the user out."""
    response.delete_cookie(
        key=_COOKIE_NAME,
        path="/",
        secure=_IS_SECURE,
        samesite="strict",
    )


@router.get(
    "/me",
    response_model=FrontendUserResponse,
    summary="Get current session info",
)
async def me(current_user: FrontendUser = CurrentUser, db: AsyncSession = DbDep):
    """Return the FrontendUser record for the authenticated session."""
    result = await db.execute(
        select(FrontendUser).where(FrontendUser.id == current_user["sub"])
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.patch(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change own password",
)
async def change_own_password(
    payload: PasswordChange,
    current_user: dict = CurrentUser,
    db: AsyncSession = DbDep,
):
    """
    Let the authenticated user change their own password.
    They must provide the current password to verify identity before updating.
    """
    result = await db.execute(
        select(FrontendUser).where(FrontendUser.id == current_user["sub"])
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )

    user.hashed_password = hash_password(payload.new_password)
    await db.commit()
    logger.info("User %r changed their password.", user.username)


# ---------------------------------------------------------------------------
# Admin-only endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/users",
    response_model=FrontendUserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminDep],
    summary="Create a frontend user account (admin only)",
)
async def create_frontend_user(payload: FrontendUserCreate, db: AsyncSession = DbDep):
    existing = await db.execute(
        select(FrontendUser).where(FrontendUser.username == payload.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username {payload.username!r} already exists.",
        )

    user = FrontendUser(
        id=uuid.uuid4(),
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_admin=payload.is_admin,
        linked_user_id=payload.linked_user_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Created frontend user %r (admin=%s).", user.username, user.is_admin)
    return user


@router.get(
    "/users",
    response_model=list[FrontendUserResponse],
    dependencies=[AdminDep],
    summary="List all frontend user accounts (admin only)",
)
async def list_frontend_users(db: AsyncSession = DbDep):
    result = await db.execute(select(FrontendUser).order_by(FrontendUser.username))
    return result.scalars().all()


@router.patch(
    "/users/{user_id}",
    response_model=FrontendUserResponse,
    dependencies=[AdminDep],
    summary="Update a frontend user's role or link (admin only)",
)
async def update_frontend_user(
    user_id: uuid.UUID,
    payload: FrontendUserUpdate,
    db: AsyncSession = DbDep,
):
    """Allow admins to toggle is_admin or change the linked_user_id for a frontend user."""
    user = await db.get(FrontendUser, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.linked_user_id is not None:
        user.linked_user_id = payload.linked_user_id
    if payload.new_password:
        user.hashed_password = hash_password(payload.new_password)
        logger.info("Admin reset password for frontend user %r.", user.username)

    await db.commit()
    await db.refresh(user)
    logger.info("Admin updated frontend user %r (is_admin=%s).", user.username, user.is_admin)
    return user


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[AdminDep],
    summary="Delete a frontend user account (admin only)",
)
async def delete_frontend_user(user_id: uuid.UUID, db: AsyncSession = DbDep):
    user = await db.get(FrontendUser, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    await db.delete(user)
    await db.commit()
