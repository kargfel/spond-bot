import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_run_warmup_caches_recipient_id():
    """run_warmup should resolve the recipient_id and write it to event.resolved_recipient_id."""
    event_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.status = "pending"
    mock_event.user_choice = "accept"
    mock_event.spond_event_id = "EVT-WARM-001"
    mock_event.resolved_recipient_id = None

    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user.profile_id = "PROF456"
    mock_user.login = "warm@example.com"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(side_effect=[mock_event, mock_user])
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.workers.executioner.AsyncSessionLocal", return_value=mock_db), \
         patch("app.workers.executioner.ensure_fresh_token",
               new_callable=AsyncMock, return_value="mock-token"), \
         patch("app.workers.executioner.spond_client.get_bulk_events",
               new_callable=AsyncMock, return_value=[{"id": "EVT-WARM-001"}]), \
         patch("app.workers.executioner.spond_client.resolve_recipient_id",
               new_callable=AsyncMock, return_value="RECIP789"):
        from app.workers.executioner import run_warmup
        await run_warmup(event_id)

    assert mock_event.resolved_recipient_id == "RECIP789"
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_warmup_skips_non_pending_event():
    """run_warmup must not touch events that are already processing/processed/failed."""
    event_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.status = "processing"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=mock_event)
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.workers.executioner.AsyncSessionLocal", return_value=mock_db), \
         patch("app.workers.executioner.ensure_fresh_token") as mock_token:
        from app.workers.executioner import run_warmup
        await run_warmup(event_id)

    mock_token.assert_not_called()


@pytest.mark.asyncio
async def test_submit_rsvp_uses_cached_recipient_id():
    """_submit_rsvp must skip get_bulk_events and resolve_recipient_id when cache is warm."""
    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.display_name = "CacheUser"
    mock_user.profile_id = "PROF999"
    mock_user.login = "cache@example.com"

    with patch("app.workers.executioner.ensure_fresh_token",
               new_callable=AsyncMock, return_value="tok"), \
         patch("app.workers.executioner.spond_client.get_bulk_events") as mock_bulk, \
         patch("app.workers.executioner.spond_client.resolve_recipient_id") as mock_resolve, \
         patch("app.workers.executioner.spond_client.rsvp", new_callable=AsyncMock):
        from app.workers.executioner import _submit_rsvp
        await _submit_rsvp(
            mock_db, mock_user, "EVT-C-001", True,
            resolved_recipient_id="CACHED-RECIP",
        )

    mock_bulk.assert_not_called()
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
async def test_submit_rsvp_falls_back_when_cache_empty():
    """_submit_rsvp must resolve normally when resolved_recipient_id is None."""
    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.display_name = "FallbackUser"
    mock_user.profile_id = "PROF000"
    mock_user.login = "fb@example.com"

    with patch("app.workers.executioner.ensure_fresh_token",
               new_callable=AsyncMock, return_value="tok"), \
         patch("app.workers.executioner.spond_client.get_bulk_events",
               new_callable=AsyncMock, return_value=[{"id": "EVT-FB-001"}]) as mock_bulk, \
         patch("app.workers.executioner.spond_client.resolve_recipient_id",
               new_callable=AsyncMock, return_value="RECIP-FB") as mock_resolve, \
         patch("app.workers.executioner.spond_client.rsvp", new_callable=AsyncMock):
        from app.workers.executioner import _submit_rsvp
        await _submit_rsvp(
            mock_db, mock_user, "EVT-FB-001", True,
            resolved_recipient_id=None,
        )

    mock_bulk.assert_called_once()
    mock_resolve.assert_called_once()


def test_schedule_warmup_adds_job():
    """schedule_warmup should add a warmup job to the scheduler."""
    scheduler = MagicMock()
    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.invite_time = datetime.now(timezone.utc) + timedelta(minutes=5)

    from app.workers.executioner import schedule_warmup
    schedule_warmup(scheduler, mock_event)

    scheduler.add_job.assert_called_once()
    call_kwargs = scheduler.add_job.call_args[1]
    assert call_kwargs["id"] == f"warmup_{mock_event.id}"


def test_cancel_warmup_removes_job():
    """cancel_warmup should suppress JobLookupError silently."""
    from apscheduler.jobstores.base import JobLookupError

    scheduler = MagicMock()
    scheduler.remove_job = MagicMock(side_effect=JobLookupError("warmup_x"))

    event_id = uuid.uuid4()
    from app.workers.executioner import cancel_warmup
    cancel_warmup(scheduler, event_id)  # must not raise
