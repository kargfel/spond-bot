# tests/test_notification_preferences.py — a member chooses which notifications they get. The choice
# lives on the account (all devices), is enforced by the server, and every change is audited.
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.spond_client import SpondAPIError
from app.models.event import Event
from app.models.frontend_user import FrontendUser
from app.models.notification_setting import PREFERENCE_KEYS, NotificationSetting
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import push
from app.workers import executioner
from tests.audit_helpers import client_factory, make_login, only, rows

ALL_ON = {k: True for k in PREFERENCE_KEYS}
FCM = "https://fcm.googleapis.com/fcm/send/phone"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/laptop"


@pytest.fixture
def push_on(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    monkeypatch.setattr(settings, "vapid_private_key", generate())


@pytest.fixture
def client(test_db, real_sessions):
    return client_factory(test_db)


# ── the API ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_login_without_saved_settings_has_everything_on(client, test_db):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        resp = await c.get("/api/v1/push/preferences")
    assert resp.status_code == 200 and resp.json() == ALL_ON
    assert set(resp.json()) == {"answer_sent", "answer_failed", "reminder_8h", "reminder_4h", "reminder_1h"}


@pytest.mark.asyncio
async def test_settings_are_saved_and_read_back(client, test_db):
    felix = await make_login(test_db, "felix")
    wanted = {**ALL_ON, "answer_sent": False, "reminder_8h": False}
    async with client(felix) as c:
        put = await c.put("/api/v1/push/preferences", json=wanted)
        got = await c.get("/api/v1/push/preferences")
    assert put.status_code == 200 and put.json() == wanted and got.json() == wanted
    row = await test_db.get(NotificationSetting, felix.id)
    assert row.as_dict() == wanted


@pytest.mark.asyncio
async def test_saving_twice_updates_the_same_row(client, test_db):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        await c.put("/api/v1/push/preferences", json={**ALL_ON, "reminder_1h": False})
        await c.put("/api/v1/push/preferences", json={**ALL_ON, "reminder_4h": False})
        got = (await c.get("/api/v1/push/preferences")).json()
    assert got == {**ALL_ON, "reminder_4h": False}
    assert len((await test_db.execute(select(NotificationSetting))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_settings_belong_to_the_account_not_to_anyone_else(client, test_db):
    felix, mara = await make_login(test_db, "felix"), await make_login(test_db, "mara")
    async with client(felix) as c:
        await c.put("/api/v1/push/preferences", json={k: False for k in PREFERENCE_KEYS})
    async with client(mara) as c:
        assert (await c.get("/api/v1/push/preferences")).json() == ALL_ON


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {k: True for k in PREFERENCE_KEYS if k != "reminder_1h"},          # a missing field would reset it silently
    {},
    {**ALL_ON, "reminder_2h": True},                                   # unknown kind
    {**ALL_ON, "answer_sent": "yes"},                                  # booleans only
    {**ALL_ON, "answer_sent": 1},
    {**ALL_ON, "answer_sent": None},
    {**ALL_ON, "reminder_4h": "false"},
])
async def test_incomplete_or_odd_settings_are_rejected_and_change_nothing(client, test_db, body):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        assert (await c.put("/api/v1/push/preferences", json=body)).status_code == 422
        assert (await c.get("/api/v1/push/preferences")).json() == ALL_ON
    assert await test_db.get(NotificationSetting, felix.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_a_session_is_required(client, method):
    async with client(None) as c:
        assert (await c.request(method, "/api/v1/push/preferences", json=ALL_ON)).status_code == 401


@pytest.mark.asyncio
async def test_a_deleted_login_cannot_save_settings(client, test_db):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        await test_db.delete(felix)
        await test_db.commit()
        assert (await c.put("/api/v1/push/preferences", json=ALL_ON)).status_code == 401


@pytest.mark.asyncio
async def test_settings_can_be_changed_without_push_being_set_up(client, test_db, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        assert (await c.put("/api/v1/push/preferences", json={**ALL_ON, "reminder_8h": False})).status_code == 200


@pytest.mark.asyncio
async def test_admins_without_a_linked_account_can_have_settings_too(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    async with client(boss) as c:
        assert (await c.put("/api/v1/push/preferences", json={**ALL_ON, "answer_failed": False})).status_code == 200


# ── audit ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_what_changed_is_recorded(audit_on, client, test_db):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        await c.put("/api/v1/push/preferences", json={**ALL_ON, "reminder_8h": False, "answer_sent": False})
        await c.put("/api/v1/push/preferences", json={**ALL_ON, "reminder_8h": False, "answer_sent": True})
        await c.put("/api/v1/push/preferences", json={**ALL_ON, "reminder_8h": False, "answer_sent": True})  # nothing new
    first, second, third = await rows(test_db, action="push.preferences_changed")
    assert first.details == {"answer_sent": {"from": True, "to": False}, "reminder_8h": {"from": True, "to": False}}
    assert second.details == {"answer_sent": {"from": False, "to": True}}
    assert third.details is None
    assert (first.actor_username, first.target_type, first.target_id) == ("felix", "login", str(felix.id))


# ── the server enforces the settings ──────────────────────────────────────


async def member(db, name="felix", *, devices=(FCM,), **prefs):
    spond = User(id=uuid.uuid4(), display_name=name.title(), login=f"{name}@example.com", encrypted_password="x", profile_id="P1")
    login = FrontendUser(id=uuid.uuid4(), username=name, hashed_password="x", linked_user_id=spond.id)
    db.add_all([spond, login])
    await db.commit()
    for endpoint in devices:
        db.add(PushSubscription(frontend_user_id=login.id, endpoint=endpoint, p256dh="k", auth="a"))
    if prefs:
        db.add(NotificationSetting(frontend_user_id=login.id, **prefs))
    await db.commit()
    return SimpleNamespace(spond=spond, login=login)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PREFERENCE_KEYS)
async def test_a_switched_off_kind_reaches_no_device(test_db, push_on, kind):
    m = await member(test_db, **{kind: False})
    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        assert await push.send_to_spond_user(test_db, m.spond.id, {"title": "x"}, kind=kind) == (0, 0)
        others = [k for k in PREFERENCE_KEYS if k != kind]
        for other in others:
            assert (await push.send_to_spond_user(test_db, m.spond.id, {"title": "x"}, kind=other))[0] == 1
    assert send.call_count == len(PREFERENCE_KEYS) - 1


@pytest.mark.asyncio
async def test_without_saved_settings_everything_is_delivered(test_db, push_on):
    m = await member(test_db)
    with patch("app.services.push.webpush_async", new_callable=AsyncMock):
        for kind in PREFERENCE_KEYS:
            assert await push.send_to_spond_user(test_db, m.spond.id, {"title": "x"}, kind=kind) == (1, 1)


@pytest.mark.asyncio
async def test_the_setting_applies_to_every_device_of_the_account(test_db, push_on):
    m = await member(test_db, devices=(FCM, MOZ), reminder_4h=False)
    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        assert await push.send_to_spond_user(test_db, m.spond.id, {"title": "x"}, kind="reminder_4h") == (0, 0)
        assert await push.send_to_spond_user(test_db, m.spond.id, {"title": "x"}, kind="reminder_8h") == (2, 2)
    assert send.call_count == 2


@pytest.mark.asyncio
async def test_one_members_setting_never_affects_another(test_db, push_on):
    quiet = await member(test_db, "mara", devices=(MOZ,), reminder_1h=False)
    loud = await member(test_db, "felix", devices=(FCM,))
    with patch("app.services.push.webpush_async", new_callable=AsyncMock):
        assert (await push.send_to_spond_user(test_db, quiet.spond.id, {"title": "x"}, kind="reminder_1h"))[0] == 0
        assert (await push.send_to_spond_user(test_db, loud.spond.id, {"title": "x"}, kind="reminder_1h"))[0] == 1


@pytest.mark.asyncio
async def test_an_unknown_kind_is_a_programming_error_not_a_silent_skip(test_db, push_on):
    m = await member(test_db)
    with pytest.raises(ValueError, match="reminder_2h"):
        await push.send_to_spond_user(test_db, m.spond.id, {"title": "x"}, kind="reminder_2h")


@pytest.mark.asyncio
async def test_the_test_notification_ignores_the_settings(client, test_db, push_on):
    felix = await make_login(test_db, "felix")
    test_db.add_all([PushSubscription(frontend_user_id=felix.id, endpoint=FCM, p256dh="k", auth="a"),
                     NotificationSetting(frontend_user_id=felix.id, **{k: False for k in PREFERENCE_KEYS})])
    await test_db.commit()
    with patch("app.services.push.webpush_async", new_callable=AsyncMock):
        async with client(felix) as c:
            resp = await c.post("/api/v1/push/test")
    assert resp.status_code == 200 and resp.json() == {"devices": 1, "delivered": 1}


# ── answers sent / failed follow the settings, through the real executioner ─


@pytest.fixture
async def rsvp_world(audit_on, push_on, test_engine, test_db, monkeypatch):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(executioner, "AsyncSessionLocal", factory)
    monkeypatch.setattr(push, "AsyncSessionLocal", factory)
    return SimpleNamespace(factory=factory, db=test_db)


async def fire(w, m, *, fail=False):
    ev = Event(id=uuid.uuid4(), spond_event_id=f"SP-{uuid.uuid4().hex[:6]}", user_id=m.spond.id, heading="Training", user_choice="accept", status="pending",
               invite_time=datetime.now(timezone.utc) - timedelta(minutes=1))
    w.db.add(ev)
    await w.db.commit()
    submit = AsyncMock(side_effect=SpondAPIError("403")) if fail else AsyncMock(return_value=datetime.now(timezone.utc))
    with patch.object(executioner, "_submit_rsvp", submit), patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await executioner._process_event(ev)
        await push.wait_for_pending()
    return send


@pytest.mark.asyncio
async def test_a_member_can_switch_off_sent_notifications_but_keep_failures(rsvp_world):
    m = await member(rsvp_world.db, answer_sent=False)
    assert (await fire(rsvp_world, m)).call_count == 0
    sent = await fire(rsvp_world, m, fail=True)
    assert sent.call_count == 1 and json.loads(sent.call_args.kwargs["data"])["outcome"] == "failed"


@pytest.mark.asyncio
async def test_a_member_can_switch_off_failure_notifications_but_keep_successes(rsvp_world):
    m = await member(rsvp_world.db, answer_failed=False)
    assert (await fire(rsvp_world, m, fail=True)).call_count == 0
    assert (await fire(rsvp_world, m)).call_count == 1


@pytest.mark.asyncio
async def test_switching_notifications_off_never_stops_the_answer_itself(rsvp_world):
    m = await member(rsvp_world.db, answer_sent=False, answer_failed=False)
    await fire(rsvp_world, m)
    assert len(await rows(rsvp_world.db, action="rsvp.sent")) == 1  # the bot's audit trail is unaffected


@pytest.mark.asyncio
async def test_the_reminder_settings_do_not_affect_answers(rsvp_world):
    m = await member(rsvp_world.db, reminder_8h=False, reminder_4h=False, reminder_1h=False)
    assert (await fire(rsvp_world, m)).call_count == 1
