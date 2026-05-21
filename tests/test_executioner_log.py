import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_write_rsvp_log_adds_row():
    from app.workers.executioner import _write_rsvp_log
    from app.models.rsvp_log import OUTCOME_SUCCESS

    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.spond_event_id = "EVT001"
    mock_event.user_choice = "accept"

    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()

    fired_at = datetime.now(timezone.utc)
    submitted_at = datetime.now(timezone.utc)

    await _write_rsvp_log(
        mock_db, mock_event, mock_user, fired_at, submitted_at, OUTCOME_SUCCESS, 0
    )

    mock_db.add.assert_called_once()
    log_row = mock_db.add.call_args[0][0]
    assert log_row.spond_event_id == "EVT001"
    assert log_row.choice == "accept"
    assert log_row.outcome == OUTCOME_SUCCESS
    assert log_row.retry_count == 0
    assert log_row.error_detail is None


@pytest.mark.asyncio
async def test_write_rsvp_log_none_user():
    from app.workers.executioner import _write_rsvp_log
    from app.models.rsvp_log import OUTCOME_FAILED

    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.spond_event_id = "EVT002"
    mock_event.user_choice = "accept"

    fired_at = datetime.now(timezone.utc)

    await _write_rsvp_log(
        mock_db, mock_event, None, fired_at, None, OUTCOME_FAILED, 0, "User not found"
    )

    log_row = mock_db.add.call_args[0][0]
    assert log_row.user_id is None
    assert log_row.error_detail == "User not found"
