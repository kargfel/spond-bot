# tests/test_reminders.py — "registration opens in 8 / 4 / 1 hours and you have not chosen yet".
# The time logic is tested as pure functions; the job runs against SQLite rows with a fixed clock
# and a faked push service.
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.event import Event
from app.models.frontend_user import FrontendUser
from app.models.notification_setting import NotificationSetting
from app.models.push_subscription import PushSubscription
from app.models.reminder_log import ReminderLog
from app.models.user import User
from app.services import push, reminders
from tests.audit_helpers import only, rows

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
M = timedelta(minutes=1)
FCM = "https://fcm.googleapis.com/fcm/send/felix-phone"


# ── pure functions ────────────────────────────────────────────────────────


@pytest.mark.parametrize("remaining,expected", [
    (timedelta(hours=9), None),            # too early
    (timedelta(hours=8, seconds=1), None),
    (timedelta(hours=8), 8),
    (timedelta(hours=7, minutes=59), 8),
    (timedelta(hours=4, seconds=1), 8),
    (timedelta(hours=4), 4),
    (timedelta(hours=2), 4),                # found late: the 4 h reminder, not the 8 h one
    (timedelta(hours=1, seconds=1), 4),
    (timedelta(hours=1), 1),
    (timedelta(minutes=30), 1),
    (timedelta(seconds=1), 1),
    (timedelta(0), None),                   # registration is open: nothing to remind about
    (timedelta(minutes=-5), None),
])
def test_the_smallest_threshold_already_reached_is_due(remaining, expected):
    assert reminders.due_threshold(remaining) == expected


@pytest.mark.parametrize("remaining,text", [
    (timedelta(hours=8), "8 hours"),
    (timedelta(hours=7, minutes=59, seconds=40), "8 hours"),
    (timedelta(hours=4), "4 hours"),
    (timedelta(hours=3, minutes=40), "4 hours"),
    (timedelta(hours=2), "2 hours"),
    (timedelta(minutes=95), "2 hours"),
    (timedelta(minutes=89), "1 hour"),
    (timedelta(minutes=59, seconds=30), "1 hour"),   # the 1 h reminder fires at 59:xx
    (timedelta(minutes=50), "1 hour"),
    (timedelta(minutes=49), "49 minutes"),
    (timedelta(minutes=1), "1 minute"),
    (timedelta(seconds=10), "1 minute"),
])
def test_remaining_time_reads_naturally(remaining, text):
    assert reminders.humanize_remaining(remaining) == text


def test_the_payload_names_the_event_and_asks_for_a_choice():
    eid = uuid.uuid4()
    p = reminders.build_reminder_payload(eid, "League match", timedelta(hours=4))
    assert p["title"] == "Registration opens in 4 hours"
    assert p["body"].startswith("League match: you haven't chosen yet")
    assert p["tag"] == f"reminder-{eid}" and p["url"] == "/dashboard" and p["outcome"] == "success"
    assert reminders.build_reminder_payload(eid, None, H)["body"].startswith("An event:")
    assert len(reminders.build_reminder_payload(eid, "x" * 900, H)["body"]) < 300


def test_the_thresholds_are_8_4_and_1_hours():
    assert reminders.THRESHOLD_HOURS == (8, 4, 1)


# ── the job ───────────────────────────────────────────────────────────────


@pytest.fixture
def push_on(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    monkeypatch.setattr(settings, "vapid_private_key", generate())


@pytest.fixture
async def world(audit_on, push_on, test_engine, test_db, monkeypatch):
    spond = User(id=uuid.uuid4(), display_name="Mara Lind", login="m@example.com", encrypted_password="x", profile_id="P1")
    login = FrontendUser(id=uuid.uuid4(), username="mara", hashed_password="x", linked_user_id=spond.id)
    test_db.add_all([spond, login])
    await test_db.commit()
    test_db.add(PushSubscription(frontend_user_id=login.id, endpoint=FCM, p256dh="k", auth="a"))
    await test_db.commit()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(reminders, "AsyncSessionLocal", factory)
    monkeypatch.setattr(push, "AsyncSessionLocal", factory)
    return SimpleNamespace(spond=spond, login=login, db=test_db)


async def add_event(w, opens_in, *, heading="League match", choice="manual", status="pending", start_in=timedelta(days=2),
                    spond=None, now=NOW):
    ev = Event(id=uuid.uuid4(), spond_event_id=f"SP-{uuid.uuid4().hex[:6]}", user_id=(spond or w.spond).id, heading=heading,
               user_choice=choice, status=status,
               invite_time=None if opens_in is None else (now + opens_in).replace(tzinfo=None),
               start_timestamp=None if start_in is None else (now + start_in).replace(tzinfo=None))
    w.db.add(ev)
    await w.db.commit()
    return ev


async def run(now=NOW):
    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        count = await reminders.run_reminders(now)
    return count, send


def titles(send):
    return [json.loads(c.kwargs["data"])["title"] for c in send.call_args_list]


@pytest.mark.asyncio
async def test_an_undecided_event_gets_one_reminder_per_threshold(world):
    ev = await add_event(world, timedelta(hours=7, minutes=30))
    assert (await run(NOW))[0] == 1                                   # 8 h threshold reached
    assert (await run(NOW + M))[0] == 0                               # same threshold: never twice
    count, send = await run(NOW + timedelta(hours=3, minutes=40))     # 3 h 50 min left
    assert titles(send) == ["Registration opens in 4 hours"] and count == 1
    count, send = await run(NOW + timedelta(hours=6, minutes=35))     # 55 min left
    assert titles(send) == ["Registration opens in 1 hour"] and count == 1
    assert (await run(NOW + timedelta(hours=7, minutes=10)))[0] == 0  # 20 min left: all three thresholds done
    logged = (await world.db.execute(select(ReminderLog.hours).where(ReminderLog.event_id == ev.id))).scalars().all()
    assert sorted(logged) == [1, 4, 8]


@pytest.mark.asyncio
async def test_the_push_carries_the_event_and_goes_to_the_members_device(world):
    ev = await add_event(world, timedelta(hours=3, minutes=40), heading="Autumn tournament")
    _, send = await run()
    (call,) = send.call_args_list
    assert call.kwargs["subscription_info"]["endpoint"] == FCM
    payload = json.loads(call.kwargs["data"])
    assert payload["title"] == "Registration opens in 4 hours"
    assert payload["body"].startswith("Autumn tournament: you haven't chosen yet")
    assert payload["tag"] == f"reminder-{ev.id}" and payload["url"] == "/dashboard"


@pytest.mark.asyncio
async def test_nothing_is_sent_while_more_than_8_hours_remain(world):
    await add_event(world, timedelta(hours=8, minutes=1))
    count, send = await run()
    assert count == 0 and send.call_count == 0


@pytest.mark.asyncio
async def test_an_event_found_late_gets_one_reminder_not_three(world):
    await add_event(world, timedelta(hours=2))
    count, send = await run()
    assert count == 1 and titles(send) == ["Registration opens in 2 hours"]
    assert (await run(NOW + M))[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,status", [("accept", "pending"), ("decline", "pending"), ("manual", "processed"),
                                           ("manual", "failed"), ("manual", "processing"), ("accept", "processed")])
async def test_decided_or_finished_events_are_left_alone(world, choice, status):
    await add_event(world, timedelta(hours=3), choice=choice, status=status)
    count, send = await run()
    assert count == 0 and send.call_count == 0


@pytest.mark.asyncio
async def test_deciding_after_a_reminder_ends_the_reminders(world):
    ev = await add_event(world, timedelta(hours=7))
    assert (await run(NOW))[0] == 1
    ev.user_choice = "accept"
    await world.db.commit()
    assert (await run(NOW + timedelta(hours=4)))[0] == 0
    assert (await run(NOW + timedelta(hours=6, minutes=30)))[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("opens_in,start_in", [
    (timedelta(minutes=-1), timedelta(days=1)),   # registration already open
    (timedelta(0), timedelta(days=1)),
    (None, timedelta(days=1)),                    # no known opening time
    (timedelta(hours=2), timedelta(hours=-1)),    # the event itself is over
])
async def test_events_without_an_upcoming_registration_are_left_alone(world, opens_in, start_in):
    await add_event(world, opens_in, start_in=start_in)
    assert (await run())[0] == 0


@pytest.mark.asyncio
async def test_paused_spond_accounts_are_not_reminded(world):
    world.spond.is_active = False
    await world.db.commit()
    await add_event(world, timedelta(hours=3))
    assert (await run())[0] == 0


@pytest.mark.asyncio
async def test_each_member_is_reminded_about_their_own_events_only(world):
    other_spond = User(id=uuid.uuid4(), display_name="Jonas", login="j@example.com", encrypted_password="x", profile_id="P2")
    other_login = FrontendUser(id=uuid.uuid4(), username="jonas", hashed_password="x", linked_user_id=other_spond.id)
    world.db.add_all([other_spond, other_login])
    await world.db.commit()
    world.db.add(PushSubscription(frontend_user_id=other_login.id, endpoint="https://fcm.googleapis.com/fcm/send/jonas", p256dh="k", auth="a"))
    await world.db.commit()
    mine = await add_event(world, timedelta(hours=3), heading="Mara's event")
    await add_event(world, timedelta(hours=6), heading="Jonas's event", spond=other_spond)
    count, send = await run()
    assert count == 2
    by_device = {c.kwargs["subscription_info"]["endpoint"]: json.loads(c.kwargs["data"]) for c in send.call_args_list}
    assert by_device[FCM]["tag"] == f"reminder-{mine.id}" and "Mara's event" in by_device[FCM]["body"]
    assert "Jonas's event" in by_device["https://fcm.googleapis.com/fcm/send/jonas"]["body"]


@pytest.mark.asyncio
async def test_several_events_each_get_their_own_reminder(world):
    await add_event(world, timedelta(hours=3), heading="A")
    await add_event(world, timedelta(hours=7), heading="B")
    count, send = await run()
    assert count == 2 and len({json.loads(c.kwargs["data"])["tag"] for c in send.call_args_list}) == 2


# ── settings ──────────────────────────────────────────────────────────────


async def prefs(w, **values):
    w.db.add(NotificationSetting(frontend_user_id=w.login.id, **values))
    await w.db.commit()


@pytest.mark.asyncio
async def test_a_reminder_the_member_switched_off_is_not_sent(world):
    await prefs(world, reminder_4h=False)
    ev = await add_event(world, timedelta(hours=7))
    assert (await run(NOW))[0] == 1                                   # 8 h: on
    assert (await run(NOW + timedelta(hours=3, minutes=40)))[0] == 0  # 4 h: off
    assert (await run(NOW + timedelta(hours=6, minutes=35)))[0] == 1  # 1 h: on
    logged = (await world.db.execute(select(ReminderLog.hours).where(ReminderLog.event_id == ev.id))).scalars().all()
    assert sorted(logged) == [1, 4, 8], "a skipped reminder is still used up: switching it on later does not resend it"


@pytest.mark.asyncio
@pytest.mark.parametrize("off", [{"reminder_8h": False}, {"reminder_1h": False}])
async def test_each_reminder_has_its_own_switch(world, off):
    await prefs(world, **off)
    hours = 7 if "reminder_8h" in off else 0.5
    await add_event(world, timedelta(hours=hours))
    assert (await run())[0] == 0


@pytest.mark.asyncio
async def test_the_answer_settings_do_not_affect_reminders(world):
    await prefs(world, answer_sent=False, answer_failed=False)
    await add_event(world, timedelta(hours=3))
    assert (await run())[0] == 1


@pytest.mark.asyncio
async def test_with_all_reminders_off_nothing_is_sent(world):
    await prefs(world, reminder_8h=False, reminder_4h=False, reminder_1h=False)
    await add_event(world, timedelta(hours=7))
    count, send = await run()
    assert count == 0 and send.call_count == 0


@pytest.mark.asyncio
async def test_a_member_with_no_device_gets_nothing_and_leaves_no_audit_noise(world):
    await world.db.execute(PushSubscription.__table__.delete())
    await world.db.commit()
    await add_event(world, timedelta(hours=3))
    count, send = await run()
    assert count == 0 and send.call_count == 0
    assert await rows(world.db, action="reminder.sent") == []


# ── at most once ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reminder_claimed_before_a_crash_is_not_sent_again(world):
    ev = await add_event(world, timedelta(hours=3))
    world.db.add(ReminderLog(event_id=ev.id, hours=4, sent_at=NOW))  # claimed, then the process died
    await world.db.commit()
    count, send = await run()
    assert count == 0 and send.call_count == 0


@pytest.mark.asyncio
async def test_claiming_the_same_reminder_twice_succeeds_only_once(world):
    ev = await add_event(world, timedelta(hours=3))
    async with reminders.AsyncSessionLocal() as a, reminders.AsyncSessionLocal() as b:
        assert await reminders._claim(a, ev.id, 4, NOW) is True
        assert await reminders._claim(b, ev.id, 4, NOW) is False
        assert await reminders._claim(b, ev.id, 1, NOW) is True


# ── robustness ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_without_push_configured_the_job_does_nothing(world, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    await add_event(world, timedelta(hours=3))
    count, send = await run()
    assert count == 0 and send.call_count == 0
    assert (await world.db.execute(select(ReminderLog))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_push_service_that_fails_never_breaks_the_job(world):
    await add_event(world, timedelta(hours=3), heading="A")
    await add_event(world, timedelta(hours=6), heading="B")
    with patch("app.services.push.webpush_async", side_effect=RuntimeError("push service down")):
        assert await reminders.run_reminders(NOW) == 2
    failed = await rows(world.db, action="reminder.sent")
    assert len(failed) == 2 and {r.outcome for r in failed} == {"failed"}


@pytest.mark.asyncio
async def test_a_broken_database_never_breaks_the_scheduler(world, monkeypatch):
    class Broken:
        async def __aenter__(self):
            raise ConnectionError("db down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(reminders, "AsyncSessionLocal", lambda: Broken())
    assert await reminders.run_reminders(NOW) == 0


# ── audit ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_sent_reminder_is_recorded_as_a_system_event(world):
    ev = await add_event(world, timedelta(hours=3, minutes=40), heading="League match")
    await run()
    row = await only(world.db, "reminder.sent")
    assert (row.actor_type, row.outcome, row.category) == ("system", "success", "reminder")
    assert (row.target_type, row.target_id, row.target_label) == ("event", str(ev.id), "League match")
    assert row.details == {"hours": 4, "member": "Mara Lind", "devices": 1, "delivered": 1}


# ── scheduling ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_job_runs_every_minute():
    from app.workers import scheduler

    scheduler.start_scheduler()
    try:
        job = scheduler.get_scheduler().get_job("reminders")
        assert job is not None and job.max_instances == 1
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["second"] == "30" and fields["minute"] == "*" and fields["hour"] == "*"
    finally:
        scheduler.shutdown_scheduler()
