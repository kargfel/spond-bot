"""
Dashboard session cookie helpers.

The session is a signed JWT in an HttpOnly cookie. Its claims (admin flag and
linked Spond account) are fixed when it is issued, so any change to them, such
as a member connecting a Spond account, must issue a fresh cookie.
"""
from fastapi import Response

from app.config import settings
from app.core.jwt import ACCESS_TOKEN_TTL, create_access_token
from app.models.frontend_user import FrontendUser

COOKIE_NAME = "sb_session"
COOKIE_MAX_AGE = int(ACCESS_TOKEN_TTL.total_seconds())
IS_SECURE = settings.site_domain != "localhost"


def set_session_cookie(response: Response, user: FrontendUser) -> None:
    token = create_access_token(
        {
            "sub": str(user.id),
            "username": user.username,
            "is_admin": user.is_admin,
            "linked_user_id": str(user.linked_user_id) if user.linked_user_id else None,
        }
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=IS_SECURE,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/", secure=IS_SECURE, samesite="strict")
