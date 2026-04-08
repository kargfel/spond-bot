"""
Token lifecycle management.

ensure_fresh_token() is the single entry point for obtaining a valid
Spond access token for a given user. Both the discovery worker and the
executioner call this before making any Spond API request.

Strategy:
  - If a token exists and is < 23 hours old  → return it as-is (no API call)
  - Otherwise                                 → re-login with stored credentials
  - On explicit force=True                    → re-login unconditionally
    (used after receiving an unexpected 401 from the Spond API)
"""
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import spond_client
from app.core.security import decrypt, encrypt
from app.models.user import User

logger = logging.getLogger(__name__)

# Re-authenticate one hour before the 24-hour token lifetime expires.
_TOKEN_MAX_AGE = timedelta(hours=23)


async def ensure_fresh_token(
    db: AsyncSession,
    user: User,
    *,
    force: bool = False,
) -> str:
    """
    Return a valid plaintext Spond access token for `user`.

    Persists a new encrypted token to the DB whenever a re-login occurs.
    The caller must NOT call db.commit() — this function handles it.
    """
    token_ok = (
        user.encrypted_access_token is not None
        and user.token_acquired_at is not None
        and (datetime.now(timezone.utc) - user.token_acquired_at) < _TOKEN_MAX_AGE
    )

    if not force and token_ok:
        return decrypt(user.encrypted_access_token)  # type: ignore[arg-type]

    logger.info(
        "Re-authenticating %r (force=%s, age=%s)...",
        user.display_name,
        force,
        (
            datetime.now(timezone.utc) - user.token_acquired_at
            if user.token_acquired_at
            else "n/a"
        ),
    )

    password = decrypt(user.encrypted_password)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
        token, acquired_at = await spond_client.login(http, user.login, password)

    user.encrypted_access_token = encrypt(token)
    user.token_acquired_at = acquired_at
    await db.commit()

    logger.info("Token refreshed for %r.", user.display_name)
    return token
