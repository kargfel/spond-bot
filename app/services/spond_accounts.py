"""
Connecting Spond accounts: verify credentials against Spond, then build the
encrypted `User` row. Shared by the admin endpoint, invite acceptance and
members connecting their own account.
"""
import logging
import uuid

import aiohttp
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import spond_client
from app.core.security import encrypt
from app.core.spond_client import SpondAuthError
from app.models.user import User

logger = logging.getLogger(__name__)


async def ensure_login_available(db: AsyncSession, login: str) -> None:
    """409 when this Spond login is already connected to SpondBot."""
    existing = await db.execute(select(User.id).where(User.login == login))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Spond account is already connected to SpondBot. Ask your admin to link it to your login.",
        )


async def connect_spond_account(login: str, password: str, display_name: str | None) -> User:
    """
    Sign in to Spond with the given credentials and return an unsaved `User`
    with the password and token encrypted. Raises 401 if Spond rejects the
    credentials and 503 if Spond cannot be reached.
    """
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
            token, acquired_at = await spond_client.login(http, login, password)
            profile_id = await spond_client.get_profile_id(http, token)
    except SpondAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Spond did not accept that login and password. Check them in the Spond app and try again.",
        ) from exc
    except Exception as exc:
        logger.error("Unexpected error verifying Spond login %r: %s", login, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Spond. Try again in a few minutes.",
        ) from exc

    return User(
        id=uuid.uuid4(),
        display_name=display_name or login.split("@")[0],
        login=login,
        encrypted_password=encrypt(password),
        encrypted_access_token=encrypt(token),
        token_acquired_at=acquired_at,
        profile_id=profile_id,
    )
