import re
import logging
from abc import ABC
from typing import Callable

import aiohttp

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when the Spond API rejects the provided credentials."""

    pass


class _SpondBase(ABC):
    """
    Abstract base class that manages authentication and the shared HTTP session
    for all Spond API interactions.
    """

    def __init__(self, username: str, password: str, member_id: str, api_url: str) -> None:
        self.username = username
        self.password = password
        self.member_id = member_id
        self.api_url = api_url
        self.clientsession = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
        self.token = None

    @property
    def auth_headers(self) -> dict:
        return {
            "content-type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "user-agent": "Spond-iOS/2.7.10 (2233; iPhone; iOS 26.2.1; Scale/3.00)",
            "Accept-Encoding": "deflate, gzip",
            "accept-language": "en",
            "priority": "u=3, i",
        }

    @staticmethod
    def require_authentication(func: Callable):
        """Decorator that ensures a valid auth token exists before each API call."""
        async def wrapper(self, *args, **kwargs):
            if not self.token:
                try:
                    await self.login()
                except AuthenticationError as e:
                    await self.clientsession.close()
                    raise e
            return await func(self, *args, **kwargs)

        return wrapper

    async def login(self) -> None:
        """
        Authenticate with the Spond API and store the session token.

        The Spond API accepts either an email address or a phone number as the
        username. The presence of "@" is used to distinguish between the two.
        """
        login_url = f"{self.api_url}login"

        if "@" in self.username:
            data = {"email": self.username, "password": self.password}
        else:
            data = {"phoneNumber": self.username, "password": self.password}

        async with self.clientsession.post(login_url, json=data) as r:
            login_result = await r.json()
            self.token = login_result.get("loginToken")
            if self.token is None:
                err_msg = f"Login failed. Response received: {login_result}"
                raise AuthenticationError(err_msg)
