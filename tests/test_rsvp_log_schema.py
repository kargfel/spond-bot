import uuid
from datetime import datetime, timezone


def test_rsvp_log_response_schema():
    from app.schemas.rsvp_log import RsvpLogResponse
    data = {
        "id": uuid.uuid4(),
        "event_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "spond_event_id": "ABC123",
        "choice": "accept",
        "fired_at": datetime.now(timezone.utc),
        "submitted_at": datetime.now(timezone.utc),
        "outcome": "success",
        "retry_count": 0,
        "error_detail": None,
    }
    obj = RsvpLogResponse(**data)
    assert obj.choice == "accept"
    assert obj.outcome == "success"


def test_rsvp_log_response_nullable_fields():
    from app.schemas.rsvp_log import RsvpLogResponse
    obj = RsvpLogResponse(
        id=uuid.uuid4(),
        event_id=None,
        user_id=None,
        spond_event_id="XYZ",
        choice="decline",
        fired_at=datetime.now(timezone.utc),
        submitted_at=None,
        outcome="failed",
        retry_count=1,
        error_detail="Network timeout",
    )
    assert obj.event_id is None
    assert obj.submitted_at is None
