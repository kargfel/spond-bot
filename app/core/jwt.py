"""
JWT helpers for the frontend auth layer.

create_access_token()  — mint a short-lived JWT for a FrontendUser
decode_access_token()  — verify and decode a JWT; returns the payload dict
                         or raises HTTPException 401 on any failure.

The secret is derived from the existing FERNET_KEY so no new env var is
required. The algorithm is HS256; tokens expire after ACCESS_TOKEN_TTL.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from app.config import settings

# 8 hours — long enough for a normal session
ACCESS_TOKEN_TTL = timedelta(hours=8)
ALGORITHM = "HS256"

# Derive a stable secret from the Fernet key (first 43 chars = 256 bits)
_SECRET = settings.fernet_key[:43]
_KEY = OctKey.import_key(_SECRET.encode("utf-8"))
_REGISTRY = jwt.JWTClaimsRegistry()


def create_access_token(data: dict) -> str:
    """Return a signed JWT containing *data* plus an `exp` claim."""
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + ACCESS_TOKEN_TTL
    return jwt.encode({"alg": ALGORITHM}, payload, _KEY)


def decode_access_token(token: str) -> dict:
    """
    Verify and decode *token*.

    Raises ``HTTP 401`` if the token is expired, malformed, or has an
    invalid signature.
    """
    try:
        decoded = jwt.decode(token, _KEY)
        _REGISTRY.validate(decoded.claims)
        return dict(decoded.claims)
    except JoseError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
