# [AUDIT FIX] Moved `import re` from inside the login() function body to the
# module top-level, as required by PEP 8. The previous placement caused a
# module dict lookup on every login call and obscured the file's dependencies.
# (audit: base.py L49)
import re
from abc import ABC
from typing import Callable
import logging

import aiohttp

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Error raised on Spond authentication failure."""

    pass


class _SpondBase(ABC):
    # [AUDIT FIX] Renamed parameter `id` to `member_id` throughout.
    # `id` is a Python built-in function; shadowing it with a parameter name
    # is confusing and can mask bugs in code that also calls the built-in.
    # (audit: base.py L13)
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
            # [AUDIT FIX] Changed accept-language from "de" (German) to "en".
            # The previous hardcoded German locale meant all API error messages
            # would be returned in German for every user, regardless of their
            # language. Defaulting to English is the open-source-friendly choice.
            # (audit: base.py L26)
            "accept-language": "en",
            "priority": "u=3, i",
        }

    @staticmethod
    def require_authentication(func: Callable):
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
        login_url = f"{self.api_url}login"

        # [AUDIT FIX] Replaced the weak regex-based email detector with a simple
        # `"@" in self.username` check. The previous pattern `[^@]+@[^@]+\.[^@]+`
        # incorrectly matched malformed strings like "a@b@c.d" and was harder to
        # read. For the sole purpose of branching between email vs. phone number,
        # the presence of "@" is the correct and sufficient discriminator.
        # (audit: base.py L50)
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
