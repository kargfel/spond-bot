"""Module contains template event data, to be used as a base when updating events."""

import copy

from jsondict import JsonDict

# Private backing store — do NOT import or mutate this directly.
# Use the get_event_template() factory function instead.
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
    Return a fresh deep copy of the event template dict.

    [AUDIT FIX] The previous pattern exposed the module-level dict directly,
    meaning any caller that mutated it (e.g. template["heading"] = "Foo") would
    permanently corrupt the shared object for every subsequent call in the same
    process. Using copy.deepcopy() here guarantees each caller gets an independent
    copy, eliminating that class of cross-call contamination bugs. (audit: event_template.py L5)
    """
    return copy.deepcopy(_EVENT_TEMPLATE)