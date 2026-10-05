from pydantic import BaseModel, StrictBool


class NotificationPreferences(BaseModel):
    """Which notifications a login wants. All fields are required: a partial update would silently reset the rest."""

    model_config = {"extra": "forbid", "from_attributes": True}

    answer_sent: StrictBool
    answer_failed: StrictBool
    reminder_8h: StrictBool
    reminder_4h: StrictBool
    reminder_1h: StrictBool
