import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.core.spond_client import SpondAPIError, SpondAuthError
from app.workers import executioner
from app.workers.executioner import _phase_timings, _retry_delay, _submit_with_retries


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(executioner.asyncio, "sleep", fake_sleep)
    return sleeps


def _event():
    ev = MagicMock()
    ev.spond_event_id = "SP-1"
    ev.resolved_recipient_id = None
    return ev


async def _run(side_effects, timings=None):
    timings = timings if timings is not None else {"retries": 0}
    sends = AsyncMock(side_effect=side_effects)
    with patch.object(executioner, "_submit_rsvp", sends):
        try:
            result = await _submit_with_retries(AsyncMock(), MagicMock(), _event(), True, timings)
        except Exception as exc:  # noqa: BLE001
            return sends, timings, exc
    return sends, timings, result


@pytest.mark.asyncio
async def test_server_error_is_retried_until_it_works(no_sleep):
    ok = datetime.now(timezone.utc)
    sends, t, result = await _run([SpondAPIError("x", 503), SpondAPIError("x", 502), ok])
    assert result == ok and t["retries"] == 2 and sends.await_count == 3
    assert no_sleep == [0.05, 0.1]


@pytest.mark.asyncio
async def test_network_error_and_timeout_are_retried():
    ok = datetime.now(timezone.utc)
    sends, t, result = await _run([asyncio.TimeoutError(), aiohttp.ServerDisconnectedError(), ok])
    assert result == ok and t["retries"] == 2


@pytest.mark.asyncio
async def test_rejected_answer_gets_only_a_few_quick_retries():
    sends, t, result = await _run([SpondAPIError("not open", 403)] * 10)
    assert isinstance(result, SpondAPIError)
    assert sends.await_count == 4 and t["retries"] == 3


@pytest.mark.asyncio
async def test_error_without_status_is_final():
    sends, t, result = await _run([SpondAPIError("Event gone")])
    assert isinstance(result, SpondAPIError) and sends.await_count == 1 and t["retries"] == 0


@pytest.mark.asyncio
async def test_401_relogs_once_without_waiting_then_reuses_recipient(no_sleep):
    ok = datetime.now(timezone.utc)
    sends, t, result = await _run([SpondAuthError("expired"), ok], {"retries": 0, "recipient_id": "M-1"})
    assert result == ok and no_sleep == []
    assert sends.await_args_list[0].kwargs["force_refresh"] is False
    assert sends.await_args_list[1].kwargs["force_refresh"] is True
    assert sends.await_args_list[1].kwargs["resolved_recipient_id"] == "M-1"


@pytest.mark.asyncio
async def test_second_401_is_final():
    sends, t, result = await _run([SpondAuthError("a"), SpondAuthError("b")])
    assert isinstance(result, SpondAuthError) and sends.await_count == 2


@pytest.mark.asyncio
async def test_after_401_the_next_transient_retry_does_not_relogin():
    ok = datetime.now(timezone.utc)
    sends, t, _ = await _run([SpondAuthError("a"), SpondAPIError("x", 500), ok])
    assert [c.kwargs["force_refresh"] for c in sends.await_args_list] == [False, True, False]


def test_transient_errors_stop_after_the_deadline():
    assert _retry_delay(SpondAPIError("x", 500), 2, 5.0) == 0.25
    assert _retry_delay(SpondAPIError("x", 500), 2, 21.0) is None
    assert _retry_delay(SpondAPIError("x", 500), 50, 1.0) == 2.0  # ladder tops out
    assert _retry_delay(ValueError("boom"), 0, 0.0) is None


def test_phase_timings_are_relative_to_opening():
    opened = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)
    ms = lambda n: opened + timedelta(milliseconds=n)  # noqa: E731
    log = MagicMock(fired_at=ms(-3))
    ev = MagicMock(invite_time=opened)
    t = {"first_sent_at": ms(12), "request_ms": 88, "done_at": ms(100), "attempts": 2}
    assert _phase_timings(log, ev, t) == {
        "fire_ms": -3, "prep_ms": 15, "request_ms": 88, "response_ms": 100, "attempts": 2, "prepared": False,
    }
    assert _phase_timings(log, ev, None) == {}
