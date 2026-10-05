"""
/auth — Frontend authentication endpoints.

POST  /auth/login           Login with username + password → sets HttpOnly session cookie
POST  /auth/logout          Clear the session cookie
GET   /auth/me              Return current user info (requires cookie)
PATCH /auth/me/password     Change own password
"""
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbDep
from app.core.rate_limit import limiter
from app.core.security import hash_password, verify_password
from app.core.session import clear_session_cookie, set_session_cookie
from app.models.frontend_user import FrontendUser
from app.services import audit
from app.schemas.auth import (
    FrontendUserResponse,
    LoginRequest,
    PasswordChange,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    summary="Login and set a secure session cookie",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("5/minute")
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
        # The attempted name is kept short: people sometimes type their password into this field.
        audit.record(
            "auth.login.failed", outcome="denied", actor=None, target_type="login",
            target_label=payload.username[:64], details={"reason": "unknown_user" if not user else "wrong_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )

    set_session_cookie(response, user)
    audit.record("auth.login.success", actor=user, target_type="login", target_id=user.id, target_label=user.username)
    logger.info("Frontend user %r logged in.", user.username)


@router.post(
    "/logout",
    summary="Clear the session cookie",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(response: Response):
    """Delete the session cookie, effectively logging the user out."""
    audit.record("auth.logout")
    clear_session_cookie(response)


@router.get(
    "/me",
    response_model=FrontendUserResponse,
    summary="Get current session info",
)
async def me(current_user: FrontendUser = CurrentUser, db: AsyncSession = DbDep):
    """Return the FrontendUser record for the authenticated session."""
    result = await db.execute(
        select(FrontendUser).where(FrontendUser.id == uuid.UUID(current_user["sub"]))
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
@limiter.limit("5/minute")
async def change_own_password(
    request: Request,
    response: Response,
    payload: PasswordChange,
    current_user: dict = CurrentUser,
    db: AsyncSession = DbDep,
):
    """
    Let the authenticated user change their own password.
    They must provide the current password to verify identity before updating.
    Every other session of this login ends; this one gets a fresh cookie and carries on.
    Rate-limited, so a stolen session cannot be used to guess the current password.
    """
    result = await db.execute(
        select(FrontendUser).where(FrontendUser.id == uuid.UUID(current_user["sub"]))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if not verify_password(payload.current_password, user.hashed_password):
        audit.record("auth.password_changed", outcome="denied", target_type="login", target_id=user.id,
                     target_label=user.username, details={"reason": "wrong_current_password"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )

    user.hashed_password = hash_password(payload.new_password)
    await db.commit()
    set_session_cookie(response, user)
    audit.record("auth.password_changed", target_type="login", target_id=user.id, target_label=user.username)
    logger.info("User %r changed their password.", user.username)
