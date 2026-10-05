"""
Shared FastAPI dependencies.

AuthDep      — verifies the Authorization: Bearer <API_KEY> header (internal use).
DbDep        — yields an async DB session.
CurrentUser  — decodes the JWT from the HttpOnly session cookie.
AdminDep     — like CurrentUser, but additionally enforces is_admin == True.
"""
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.jwt import decode_access_token
from app.core.security import password_version
from app.database import AsyncSessionLocal, get_db
from app.models.frontend_user import FrontendUser

_bearer = HTTPBearer(auto_error=True)

# ---------------------------------------------------------------------------
# Internal API-key gate (used by the scheduler / worker trigger endpoints)
# ---------------------------------------------------------------------------


async def _require_api_key(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    if creds.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


# ---------------------------------------------------------------------------
# Frontend JWT gate — reads from the HttpOnly 'sb_session' cookie
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_session():
    """A short-lived session just for checking the login (a seam for tests)."""
    async with AsyncSessionLocal() as db:
        yield db


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def _get_current_user(
    sb_session: str | None = Cookie(default=None),
) -> dict:
    """
    The signed-in login, checked against the database on every request.

    The cookie only proves who the session was issued to. What the login may do is read
    fresh from its row, so a demoted admin loses admin rights at once, a deleted login is
    locked out at once, and a password change ends every session issued before it.

    Uses its own short session, not the request's: streaming endpoints (SSE) stay open for
    hours and must not hold a database connection while they do.
    """
    if not sb_session:
        raise _unauthorized("Not authenticated.")
    claims = decode_access_token(sb_session)
    try:
        login_id = uuid.UUID(str(claims.get("sub")))
    except ValueError:
        raise _unauthorized("Invalid or expired session. Please log in again.")

    async with open_session() as db:
        login = await db.get(FrontendUser, login_id)
        if login is None:
            raise _unauthorized("This login no longer exists.")
        # Sessions issued before this check existed carry no `pwv`; they run out within hours.
        if "pwv" in claims and claims["pwv"] != password_version(login.hashed_password):
            raise _unauthorized("Your password was changed. Please log in again.")
        return {
            **claims,
            "username": login.username,
            "is_admin": login.is_admin,
            "linked_user_id": str(login.linked_user_id) if login.linked_user_id else None,
        }


async def _require_admin(current_user: dict = Depends(_get_current_user)) -> dict:
    if not current_user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Public dependency aliases — import these in route modules
# ---------------------------------------------------------------------------

# Use these as router-level or endpoint-level dependencies
AuthDep = Depends(_require_api_key)
DbDep = Depends(get_db)

# Inject as a typed parameter, e.g.:  current_user: dict = CurrentUser
CurrentUser = Depends(_get_current_user)
AdminDep    = Depends(_require_admin)
