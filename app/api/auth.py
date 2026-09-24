"""
/auth — Frontend authentication endpoints.

POST  /auth/login           Login with username + password → sets HttpOnly session cookie
POST  /auth/logout          Clear the session cookie
GET   /auth/me              Return current user info (requires cookie)
PATCH /auth/me/password     Change own password
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbDep
from app.config import settings
from app.core.jwt import ACCESS_TOKEN_TTL, create_access_token
from app.core.security import hash_password, verify_password
from app.models.frontend_user import FrontendUser
from app.schemas.auth import (
    FrontendUserResponse,
    LoginRequest,
    PasswordChange,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

_limiter = Limiter(key_func=get_remote_address)

# Cookie name and settings
_COOKIE_NAME = "sb_session"
_COOKIE_MAX_AGE = int(ACCESS_TOKEN_TTL.total_seconds())
_IS_SECURE = settings.site_domain != "localhost"


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
