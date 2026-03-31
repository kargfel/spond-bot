import logging
from base import _SpondBase
from datetime import datetime
from typing import ClassVar
from jsondict import JsonDict

# [AUDIT FIX] Removed the redundant `from datetime import datetime` inside the
# TYPE_CHECKING block. datetime was already imported unconditionally on the line
# above; the duplicate import was a confusing artefact. (audit: spond.py L9-10)

logger = logging.getLogger(__name__)


class Spond(_SpondBase):
    """Main class for interacting with the Spond API."""
    _API_BASE_URL = "https://api.spond.com/core/v1/"

    # [AUDIT FIX] Updated _DT_FORMAT to use the actual H:M:S components instead
    # of a hardcoded midnight value ("T00:00:00.000Z"). The previous format meant
    # that minEndTimestamp was always truncated to midnight, making time-of-day
    # filtering inaccurate. (audit: spond.py L16)
    _DT_FORMAT: ClassVar = "%Y-%m-%dT%H:%M:%S.000Z"

    # [AUDIT FIX] Renamed `id` parameter to `member_id` to be consistent with
    # the rename in _SpondBase and to stop shadowing the Python built-in. (audit: base.py L13)
    def __init__(self, username: str, password: str, member_id: str) -> None:
        super().__init__(username, password, member_id, self._API_BASE_URL)


    @_SpondBase.require_authentication
    async def get_upcoming_events(
        self,
        includeDeclined: bool | None = None,
        minEndTimestamp: datetime | None = None,
    ) -> list[JsonDict]:
        """
        Retrieve events.

        Parameters
        ----------
        includeDeclined : bool, optional
            Include declined events.
            Uses `includeDeclined` API parameter.
        minEndTimestamp : datetime, optional
            Only include events which end at or after this datetime.
            Uses `minEndTimestamp` API parameter; relates to `endTimestamp` event
            attribute.

        Returns
        -------
        list[JSONDict]
            A list of events, each represented as a dictionary.

        Raises
        ------
        ValueError
            Raised when the request to the API fails. This occurs if the response
            status code indicates an error (e.g., 4xx or 5xx). The error message
            includes the HTTP status code and the response body for debugging purposes.
        """
        # [AUDIT FIX] Removed `| None` from the return type. This method always
        # returns a list (possibly empty) or raises ValueError; it never returns
        # None. The incorrect annotation forced callers to guard against None
        # unnecessarily. (audit: spond.py L27)
        url = f"{self._API_BASE_URL}sponds/upcoming"
        params = {}
        if includeDeclined is not None:
            params["includeDeclined"] = str(includeDeclined).lower()
        if minEndTimestamp:
            params["minEndTimestamp"] = minEndTimestamp.strftime(self._DT_FORMAT)

        async with self.clientsession.get(
            url, headers=self.auth_headers, params=params
        ) as r:
            if not r.ok:
                error_details = await r.text()
                raise ValueError(
                    f"Request failed with status {r.status}: {error_details}"
                )
            self.events = await r.json()
            return self.events

    @_SpondBase.require_authentication
    async def get_groups(self) -> list[JsonDict]:
        """
        Retrieve groups.

        Returns
        -------
        list[JSONDict]
            A list of groups, each represented as a dictionary.

        Raises
        ------
        ValueError
            Raised when the request to the API fails.
        """
        # [AUDIT FIX] Removed `| None` from return type — same reason as above.
        url = f"{self._API_BASE_URL}groups"
        async with self.clientsession.get(
            url, headers=self.auth_headers
        ) as r:
            if not r.ok:
                error_details = await r.text()
                raise ValueError(
                    f"Request failed with status {r.status}: {error_details}"
                )
            return await r.json()

    @_SpondBase.require_authentication
    async def give_answer(
        self,
        event_id: str,
        answer: bool,
    ) -> None:
        """
        Give an answer to an event.

        Parameters
        ----------
        event_id : str
            The ID of the event to which the answer should be given.
        answer : bool
            The answer to the event. Can only be true or false.

        Returns
        -------
        None
            None if the request was successful.

        Raises
        ------
        ValueError
            Raised when the request to the API fails. This occurs if the response
            status code indicates an error (e.g., 4xx or 5xx). The error message
            includes the HTTP status code and the response body for debugging purposes.
        """
        # [AUDIT FIX] self.id → self.member_id, consistent with the rename in _SpondBase.
        url = f"{self._API_BASE_URL}sponds/{event_id}/responses/{self.member_id}"
        data = {
            "accepted": answer
        }
        # [AUDIT FIX] Removed `print(url, data)` debug statement that leaked the
        # user's Spond Member ID and RSVP answer to stdout on every invocation.
        # Replaced with a DEBUG-level log call that is suppressed at the default
        # INFO level, so it is invisible in production but available for debugging.
        # (audit: spond.py L131)
        logger.debug("Submitting RSVP to %s with payload: %s", url, data)
        async with self.clientsession.put(
            url, headers=self.auth_headers, json=data
        ) as r:
            if not r.ok:
                error_details = await r.text()
                raise ValueError(
                    f"Request failed with status {r.status}: {error_details}"
                )
