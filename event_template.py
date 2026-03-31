"""Template structure for Spond event payloads."""

import copy

from jsondict import JsonDict

# Internal template — not intended for direct import.
# Use get_event_template() to obtain a fresh, independent copy.
_EVENT_TEMPLATE: JsonDict = {
    "heading": None,
    "description": None,
    "spondType": "EVENT",
    "startTimestamp": None,
    "endTimestamp": None,
    "commentsDisabled": False,
    "maxAccepted": 0,
    "rsvpDate": None,
    "location": {
        "id": None,
        "feature": None,
        "address": None,
        "latitude": None,
        "longitude": None,
    },
    "owners": [{"id": None}],
    "visibility": "INVITEES",
    "participantsHidden": False,
    "autoReminderType": "DISABLED",
    "autoAccept": False,
    "payment": {},
    "attachments": [],
    "id": None,
    "tasks": {
        "openTasks": [],
        "assignedTasks": [
            {
                "name": None,
                "description": "",
                "type": "ASSIGNED",
                "id": None,
                "adultsOnly": True,
                "assignments": {"memberIds": [], "profiles": [], "remove": []},
            }
        ],
    },
}


def get_event_template() -> JsonDict:
    """
    Return a fresh copy of the default event template.

    Always use this function instead of importing the module-level dict directly.
    Each call returns an independent deep copy, so modifications made by one
    caller do not affect subsequent callers.
    """
    return copy.deepcopy(_EVENT_TEMPLATE)