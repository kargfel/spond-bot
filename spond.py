import logging
from base import _SpondBase
from datetime import datetime
from typing import ClassVar
from jsondict import JsonDict

logger = logging.getLogger(__name__)


class Spond(_SpondBase):
    """Main class for interacting with the Spond API."""

    _API_BASE_URL = "https://api.spond.com/core/v1/"

    # Timestamp format expected by the Spond API.
    _DT_FORMAT: ClassVar = "%Y-%m-%dT%H:%M:%S.000Z"

    def __init__(self, username: str, password: str, member_id: str) -> None:
        super().__init__(username, password, member_id, self._API_BASE_URL)


    @_SpondBase.require_authentication
    async def get_upcoming_events(
        self,
        includeDeclined: bool | None = None,
        minEndTimestamp: datetime | None = None,
    ) -> list[JsonDict]:
        """
        Retrieve upcoming events for the authenticated user.

        Parameters
        ----------
        includeDeclined : bool, optional
            When True, includes events the user has already declined.
        minEndTimestamp : datetime, optional
            Only return events whose end time is at or after this datetime.

        Returns
        -------
        list[JsonDict]
            A list of events, each represented as a dictionary.

        Raises
        ------
        ValueError
            Raised when the API returns a non-2xx response. The message includes
            the HTTP status code and response body for debugging.
        """
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
        Retrieve all groups the authenticated user belongs to.

        Returns
        -------
        list[JsonDict]
            A list of groups, each represented as a dictionary.

        Raises
        ------
        ValueError
            Raised when the API returns a non-2xx response.
        """
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
        Submit an RSVP response for a given event.

        Parameters
        ----------
        event_id : str
            The unique ID of the event to respond to.
        answer : bool
            True to accept, False to decline.

        Raises
        ------
        ValueError
            Raised when the API returns a non-2xx response. The message includes
            the HTTP status code and response body for debugging.
        """
        url = f"{self._API_BASE_URL}sponds/{event_id}/responses/{self.member_id}"
        data = {"accepted": answer}
        logger.debug("Submitting RSVP to %s with payload: %s", url, data)
        async with self.clientsession.put(
            url, headers=self.auth_headers, json=data
        ) as r:
            if not r.ok:
                error_details = await r.text()
                raise ValueError(
                    f"Request failed with status {r.status}: {error_details}"
                )
