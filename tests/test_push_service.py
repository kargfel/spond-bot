# tests/test_push_service.py — Web Push delivery: configuration, payloads, sending, cleanup.
import asyncio
import base64
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pywebpush import WebPushException
from sqlalchemy import select

from app.config import settings
from app.models.frontend_user import FrontendUser
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import push

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/xyz"
APPLE = "https://web.push.apple.com/QOk123"


def make_key() -> str:
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    return generate()


@pytest.fixture
def vapid_key(monkeypatch):
    key = make_key()
    monkeypatch.setattr(settings, "vapid_private_key", key)
    return key


async def make_member(db, name="felix", linked=True):
    spond = None
    if linked:
        spond = User(id=uuid.uuid4(), display_name=name.title(), login=f"{name}@example.com", encrypted_password="x")
        db.add(spond)
    login = FrontendUser(id=uuid.uuid4(), username=name, hashed_password="x", linked_user_id=spond.id if spond else None)
    db.add(login)
    await db.commit()
    return login, spond


async def subscribe(db, login, endpoint=FCM):
    sub = PushSubscription(frontend_user_id=login.id, endpoint=endpoint, p256dh="p256dh-key", auth="auth-key")
    db.add(sub)
    await db.commit()
    return sub


def http_error(status):
    return WebPushException("Push failed", response=SimpleNamespace(status=status))


# ── Configuration ─────────────────────────────────────────────────────────


def test_push_is_off_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    assert push.push_enabled() is False
    assert push.public_key() is None


def test_an_invalid_key_disables_push_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "not-a-key")
    assert push.push_enabled() is False
    assert push.public_key() is None


def test_public_key_is_derived_from_the_private_key(vapid_key):
    assert push.push_enabled() is True
    pub = push.public_key()
    raw = base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
    assert len(raw) == 65 and raw[0] == 4  # uncompressed P-256 point, what browsers expect
    assert "=" not in pub and "+" not in pub and "/" not in pub


def test_the_same_key_always_gives_the_same_public_key(vapid_key):
    assert push.public_key() == push.public_key()


def test_subject_defaults_to_the_site_domain(monkeypatch):
    monkeypatch.setattr(settings, "vapid_subject", "")
    monkeypatch.setattr(settings, "site_domain", "bot.example.com")
    assert push._subject() == "https://bot.example.com"
    monkeypatch.setattr(settings, "vapid_subject", "mailto:me@example.com")
    assert push._subject() == "mailto:me@example.com"


def test_generated_keys_are_unique_and_usable():
    assert make_key() != make_key()


# ── Endpoint allow-list ───────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint", [FCM, MOZ, APPLE, "https://wns2-par02p.notify.windows.com/w/?token=x",
                                      "https://FCM.GoogleAPIs.com/x", "https://fcm.googleapis.com:443/x"])
def test_known_push_services_are_allowed(endpoint):
    assert push.is_allowed_endpoint(endpoint)


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/x",  # not https
    "https://evil.example/fcm.googleapis.com",  # name only in the path
    "https://fcm.googleapis.com.evil.example/x",  # look-alike suffix
    "https://evilfcm.googleapis.com.example/x",
    "https://user:pw@fcm.googleapis.com/x",  # credentials in the URL
    "https://fcm.googleapis.com:8443/x",  # unusual port
    "https://169.254.169.254/latest/meta-data",
    "https://localhost/x",
    "https://127.0.0.1/x",
    "ftp://fcm.googleapis.com/x",
    "javascript:alert(1)",
    "",
    "not a url",
    "https://[::1/x",
])
def test_anything_else_is_rejected(endpoint):
    assert not push.is_allowed_endpoint(endpoint)


# ── Payloads ──────────────────────────────────────────────────────────────


def test_success_payload_names_the_answer_and_the_event():
    p = push.build_rsvp_payload("e1", "Training, Hall B", "accept", "success")
    assert p == {"tag": "rsvp-e1", "url": "/dashboard", "outcome": "success",
                 "title": "Answer sent: Going", "body": "Training, Hall B"}
    assert push.build_rsvp_payload("e1", "x", "decline", "success")["title"] == "Answer sent: Not going"


def test_failure_payload_points_the_member_to_the_app():
    p = push.build_rsvp_payload("e2", "League match", "accept", "failed")
    assert p["outcome"] == "failed"
    assert p["title"] == "SpondBot couldn't answer"
    assert p["body"] == "League match. Open SpondBot to retry."


def test_payload_copes_with_missing_and_very_long_headings():
    assert push.build_rsvp_payload("e", None, "accept", "success")["body"] == "Event"
    assert len(push.build_rsvp_payload("e", "x" * 900, "accept", "success")["body"]) == 120


def test_the_payload_always_fits_a_push_message():
    # Push services cap payloads at about 4 KB
    p = push.build_rsvp_payload(uuid.uuid4(), "ä" * 900, "accept", "failed")
    assert len(json.dumps(p).encode()) < 1000


# ── Sending ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notifies_every_device_of_the_linked_login_only(test_db, vapid_key):
    felix, felix_spond = await make_member(test_db, "felix")
    mara, _ = await make_member(test_db, "mara")
    await subscribe(test_db, felix, FCM)
    await subscribe(test_db, felix, MOZ)
    await subscribe(test_db, mara, APPLE)

    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        devices, delivered = await push.send_to_spond_user(test_db, felix_spond.id, {"title": "Hi"})

    assert (devices, delivered) == (2, 2)
    assert {c.kwargs["subscription_info"]["endpoint"] for c in send.call_args_list} == {FCM, MOZ}


@pytest.mark.asyncio
async def test_each_message_is_signed_and_carries_the_payload(test_db, vapid_key, monkeypatch):
    monkeypatch.setattr(settings, "vapid_subject", "mailto:ops@example.com")
    felix, spond = await make_member(test_db)
    await subscribe(test_db, felix)

    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        await push.send_to_spond_user(test_db, spond.id, {"title": "Answer sent: Going", "tag": "rsvp-1"})

    kw = send.call_args.kwargs
    assert kw["subscription_info"] == {"endpoint": FCM, "keys": {"p256dh": "p256dh-key", "auth": "auth-key"}}
    assert json.loads(kw["data"]) == {"title": "Answer sent: Going", "tag": "rsvp-1"}
    assert kw["vapid_claims"] == {"sub": "mailto:ops@example.com"}
    assert kw["ttl"] == push.TTL_SECONDS
    assert kw["headers"] == {"Urgency": "high"}
    assert kw["vapid_private_key"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_subscriptions_the_browser_dropped_are_deleted(test_db, vapid_key, status):
    felix, spond = await make_member(test_db)
    await subscribe(test_db, felix, FCM)
    await subscribe(test_db, felix, MOZ)

    async def fake(**kw):
        if kw["subscription_info"]["endpoint"] == FCM:
            raise http_error(status)

    with patch("app.services.push.webpush_async", side_effect=fake):
        devices, delivered = await push.send_to_spond_user(test_db, spond.id, {"title": "x"})

    assert (devices, delivered) == (2, 1)
    left = (await test_db.execute(select(PushSubscription.endpoint))).scalars().all()
    assert left == [MOZ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [http_error(500), http_error(429), http_error(413), RuntimeError("boom"),
                                     asyncio.TimeoutError()])
async def test_other_failures_keep_the_subscription_and_never_raise(test_db, vapid_key, failure):
    felix, spond = await make_member(test_db)
    await subscribe(test_db, felix)

    with patch("app.services.push.webpush_async", side_effect=failure):
        devices, delivered = await push.send_to_spond_user(test_db, spond.id, {"title": "x"})

    assert (devices, delivered) == (1, 0)
    assert len((await test_db.execute(select(PushSubscription))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_one_broken_device_does_not_block_the_others(test_db, vapid_key):
    felix, spond = await make_member(test_db)
    await subscribe(test_db, felix, FCM)
    await subscribe(test_db, felix, MOZ)

    async def fake(**kw):
        if kw["subscription_info"]["endpoint"] == FCM:
            raise RuntimeError("down")

    with patch("app.services.push.webpush_async", side_effect=fake) as send:
        _, delivered = await push.send_to_spond_user(test_db, spond.id, {"title": "x"})
    assert delivered == 1 and send.call_count == 2


@pytest.mark.asyncio
async def test_nothing_is_sent_when_push_is_off(test_db, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    felix, spond = await make_member(test_db)
    await subscribe(test_db, felix)

    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        assert await push.send_to_spond_user(test_db, spond.id, {"title": "x"}) == (1, 0)
    send.assert_not_called()


@pytest.mark.asyncio
async def test_a_login_without_a_linked_spond_account_gets_no_answer_notifications(test_db, vapid_key):
    admin, _ = await make_member(test_db, "admin", linked=False)
    _, felix_spond = await make_member(test_db, "felix")
    await subscribe(test_db, admin)

    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        assert await push.send_to_spond_user(test_db, felix_spond.id, {"title": "x"}) == (0, 0)
    send.assert_not_called()


@pytest.mark.asyncio
async def test_send_to_login_reaches_that_logins_devices(test_db, vapid_key):
    felix, _ = await make_member(test_db, "felix")
    mara, _ = await make_member(test_db, "mara")
    await subscribe(test_db, felix, FCM)
    await subscribe(test_db, mara, MOZ)

    with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
        assert await push.send_to_login(test_db, felix.id, {"title": "t"}) == (1, 1)
    assert send.call_args.kwargs["subscription_info"]["endpoint"] == FCM


# ── Background dispatch ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_does_nothing_when_push_is_off(monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    with patch("app.services.push.notify_rsvp", new_callable=AsyncMock) as notify:
        push.dispatch_rsvp_notification(uuid.uuid4(), uuid.uuid4(), "x", "accept", "success")
        await push.wait_for_pending()
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_runs_in_the_background_and_can_be_awaited(vapid_key):
    with patch("app.services.push.notify_rsvp", new_callable=AsyncMock) as notify:
        uid, eid = uuid.uuid4(), uuid.uuid4()
        push.dispatch_rsvp_notification(uid, eid, "Training", "accept", "success")
        await push.wait_for_pending()
    notify.assert_awaited_once_with(uid, eid, "Training", "accept", "success")
    assert not push._tasks


@pytest.mark.asyncio
async def test_notify_never_raises_even_when_the_database_is_down(vapid_key):
    class Broken:
        async def __aenter__(self):
            raise ConnectionError("db down")

        async def __aexit__(self, *a):
            return False

    with patch("app.services.push.AsyncSessionLocal", return_value=Broken()):
        await push.notify_rsvp(uuid.uuid4(), uuid.uuid4(), "x", "accept", "success")
