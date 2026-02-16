from base import _SpondBase

from datetime import datetime

from typing import TYPE_CHECKING, ClassVar
from jsondict import JsonDict
from event_template import _EVENT_TEMPLATE

if TYPE_CHECKING:
    from datetime import datetime


class Spond(_SpondBase):
    """Main class for interacting with the Spond API."""
    _API_BASE_URL = "https://api.spond.com/core/v1/"
    _DT_FORMAT: ClassVar = "%Y-%m-%dT00:00:00.000Z"

    def __init__(self, username: str, password: str, id: str) -> None:
        super().__init__(username, password, id, self._API_BASE_URL)
    

    @_SpondBase.require_authentication
    async def get_upcoming_events(
        self, 
        includeDeclined: bool | None = None,
        minEndTimestamp: datetime | None = None,
    ) -> list[JsonDict] | None:
        """
        Retrieve events.

        Parameters
        ----------
        includeDeclined : bool, optional
            Include declined events.
            Uses `includeDeclined` API parameter.
        minEndTimestamp : datetime, optional
            Only include events which end before or at this datetime.
            Uses `maxEndTimestamp` API parameter; relates to `endTimestamp` event
            attribute.

        Returns
        -------
        list[JSONDict] or None
             A list of events, each represented as a dictionary, or None if no events
             are available.

        Raises
        ------
        ValueError
            Raised when the request to the API fails. This occurs if the response
            status code indicates an error (e.g., 4xx or 5xx). The error message
            includes the HTTP status code and the response body for debugging purposes.
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
        url = f"{self._API_BASE_URL}sponds/{event_id}/responses/{self.id}"
        data = {
            "accepted": answer
        }
        print(url, data)
        async with self.clientsession.put(
            url, headers=self.auth_headers, json=data
        ) as r:
            if not r.ok:
                error_details = await r.text()
                raise ValueError(
                    f"Request failed with status {r.status}: {error_details}"
                )
                
