import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_process_event_skips_when_already_claimed():
    """If the atomic UPDATE claims 0 rows, _process_event must not submit the RSVP."""
    mock_result = MagicMock()
    mock_result.rowcount = 0

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()

    with patch("app.workers.executioner.AsyncSessionLocal", return_value=mock_db), \
         patch("app.workers.executioner._submit_rsvp") as mock_submit:
        from app.workers.executioner import _process_event
        await _process_event(mock_event)
        mock_submit.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_submits_when_claim_succeeds():
    """If the atomic UPDATE claims 1 row, _process_event must proceed to submit."""
    mock_result = MagicMock()
    mock_result.rowcount = 1

    mock_db_event = MagicMock()
    mock_db_event.id = uuid.uuid4()
    mock_db_event.user_id = uuid.uuid4()
    mock_db_event.spond_event_id = "EVT-CLAIM-001"
    mock_db_event.user_choice = "accept"
    mock_db_event.heading = "Test"
    mock_db_event.status = "processing"
    mock_db_event.resolved_recipient_id = None

    mock_user = MagicMock()
    mock_user.display_name = "Alice"
    mock_user.profile_id = "PROFABC"

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.get = AsyncMock(side_effect=[mock_db_event, mock_user])
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    mock_event = MagicMock()
    mock_event.id = mock_db_event.id

    fake_submitted_at = datetime.now(timezone.utc)

    with patch("app.workers.executioner.AsyncSessionLocal", return_value=mock_db), \
         patch("app.workers.executioner._submit_rsvp", new_callable=AsyncMock,
               return_value=fake_submitted_at) as mock_submit, \
         patch("app.workers.executioner._write_rsvp_log", new_callable=AsyncMock):
        from app.workers.executioner import _process_event
        await _process_event(mock_event)
        mock_submit.assert_called_once()
