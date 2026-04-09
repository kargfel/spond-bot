"""
Shared FastAPI dependencies.

AuthDep      — verifies the Authorization: Bearer <API_KEY> header (internal use).
DbDep        — yields an async DB session.
CurrentUser  — decodes the JWT from the HttpOnly session cookie.
AdminDep     — like CurrentUser, but additionally enforces is_admin == True.
"""
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.jwt import decode_access_token
from app.database import get_db

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


async def _get_current_user(
    sb_session: str | None = Cookie(default=None),
) -> dict:
    """Decode the JWT from the session cookie and return the payload as a plain dict."""
    if not sb_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )
    return decode_access_token(sb_session)


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
