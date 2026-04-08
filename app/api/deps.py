"""
Shared FastAPI dependencies.

AuthDep  — verifies the Authorization: Bearer <API_KEY> header on every request.
DbDep    — yields an async DB session from the connection pool.
"""
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

_bearer = HTTPBearer(auto_error=True)


async def _require_api_key(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    if creds.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


# Use these as router-level or endpoint-level dependencies
AuthDep = Depends(_require_api_key)
DbDep = Depends(get_db)
