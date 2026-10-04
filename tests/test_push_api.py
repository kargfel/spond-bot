# tests/test_push_api.py — /api/v1/push: availability, registering and removing devices, test sends.
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.frontend_user import FrontendUser
from app.models.push_subscription import PushSubscription

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/xyz"
BODY = {"endpoint": FCM, "keys": {"p256dh": "BNc-p256dh", "auth": "auth-secret"}}


@pytest.fixture
def push_on(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    monkeypatch.setattr(settings, "vapid_private_key", generate())


@pytest.fixture
async def felix(test_db):
    login = FrontendUser(id=uuid.uuid4(), username="felix", hashed_password="x")
    test_db.add(login)
    await test_db.commit()
    return {"sub": str(login.id), "username": "felix", "is_admin": False, "linked_user_id": None}


@pytest.fixture
async def mara(test_db):
    login = FrontendUser(id=uuid.uuid4(), username="mara", hashed_password="x")
    test_db.add(login)
    await test_db.commit()
    return {"sub": str(login.id), "username": "mara", "is_admin": False, "linked_user_id": None}


async def rows(db):
    return (await db.execute(select(PushSubscription).order_by(PushSubscription.created_at))).scalars().all()


# ── config ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_reports_the_public_key_when_push_is_set_up(client_as, felix, push_on):
    async with client_as(felix) as c:
        data = (await c.get("/api/v1/push/config")).json()
    assert data["enabled"] is True
    assert len(data["public_key"]) == 87  # 65 bytes, base64url without padding


@pytest.mark.asyncio
async def test_config_says_disabled_without_a_key(client_as, felix, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    async with client_as(felix) as c:
        assert (await c.get("/api/v1/push/config")).json() == {"enabled": False, "public_key": None}


@pytest.mark.asyncio
async def test_the_private_key_never_leaves_the_server(client_as, felix, push_on):
    async with client_as(felix) as c:
        text = (await c.get("/api/v1/push/config")).text
    assert settings.vapid_private_key not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("GET", "/config"), ("POST", "/subscribe"), ("POST", "/unsubscribe"), ("POST", "/test")])
async def test_everything_requires_a_session(client_as, push_on, method, path):
    async with client_as(None) as c:
        resp = await c.request(method, "/api/v1/push" + path, json=BODY)
    assert resp.status_code == 401


# ── subscribe ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_stores_the_device_for_the_caller(client_as, test_db, felix, push_on):
    async with client_as(felix) as c:
        resp = await c.post("/api/v1/push/subscribe", json=BODY, headers={"user-agent": "Mozilla/5.0 Test"})
    assert resp.status_code == 204
    (sub,) = await rows(test_db)
    assert str(sub.frontend_user_id) == felix["sub"]
    assert (sub.endpoint, sub.p256dh, sub.auth) == (FCM, "BNc-p256dh", "auth-secret")
    assert sub.user_agent == "Mozilla/5.0 Test"


@pytest.mark.asyncio
async def test_subscribing_twice_updates_instead_of_duplicating(client_as, test_db, felix, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        rotated = {"endpoint": FCM, "keys": {"p256dh": "new-key", "auth": "new-auth"}}
        assert (await c.post("/api/v1/push/subscribe", json=rotated)).status_code == 204
    (sub,) = await rows(test_db)
    assert (sub.p256dh, sub.auth) == ("new-key", "new-auth")


@pytest.mark.asyncio
async def test_a_device_moves_to_whoever_signs_in_on_it(client_as, test_db, felix, mara, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
    async with client_as(mara) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
    (sub,) = await rows(test_db)
    assert str(sub.frontend_user_id) == mara["sub"]


@pytest.mark.asyncio
async def test_one_login_can_have_several_devices(client_as, test_db, felix, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        await c.post("/api/v1/push/subscribe", json={**BODY, "endpoint": MOZ})
    assert {r.endpoint for r in await rows(test_db)} == {FCM, MOZ}


@pytest.mark.asyncio
async def test_a_login_keeps_only_its_newest_devices(client_as, test_db, felix, push_on, monkeypatch):
    from app.services import push

    monkeypatch.setattr(push, "MAX_DEVICES_PER_LOGIN", 3)
    async with client_as(felix) as c:
        for i in range(5):
            await c.post("/api/v1/push/subscribe", json={**BODY, "endpoint": f"https://fcm.googleapis.com/fcm/send/dev{i}"})
    kept = {r.endpoint.rsplit("/", 1)[1] for r in await rows(test_db)}
    assert len(kept) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [
    "https://evil.example/collect", "http://fcm.googleapis.com/x", "https://169.254.169.254/",
    "https://fcm.googleapis.com.evil.example/x", "javascript:alert(1)", "", "x" * 3000,
])
async def test_endpoints_that_are_not_push_services_are_rejected(client_as, test_db, felix, push_on, endpoint):
    async with client_as(felix) as c:
        resp = await c.post("/api/v1/push/subscribe", json={**BODY, "endpoint": endpoint})
    assert resp.status_code == 422
    assert await rows(test_db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"endpoint": FCM}, {"endpoint": FCM, "keys": {}}, {"endpoint": FCM, "keys": {"p256dh": "a"}},
    {"endpoint": FCM, "keys": {"p256dh": "", "auth": "b"}}, {"keys": BODY["keys"]}, {},
])
async def test_incomplete_subscriptions_are_rejected(client_as, test_db, felix, push_on, body):
    async with client_as(felix) as c:
        assert (await c.post("/api/v1/push/subscribe", json=body)).status_code == 422
    assert await rows(test_db) == []


@pytest.mark.asyncio
async def test_browsers_extra_fields_are_accepted(client_as, felix, push_on):
    body = {**BODY, "expirationTime": None}
    async with client_as(felix) as c:
        assert (await c.post("/api/v1/push/subscribe", json=body)).status_code == 204


@pytest.mark.asyncio
async def test_subscribing_is_refused_when_push_is_off(client_as, test_db, felix, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    async with client_as(felix) as c:
        resp = await c.post("/api/v1/push/subscribe", json=BODY)
    assert resp.status_code == 503
    assert await rows(test_db) == []


@pytest.mark.asyncio
async def test_a_deleted_account_cannot_subscribe(client_as, test_db, push_on):
    ghost = {"sub": str(uuid.uuid4()), "username": "ghost", "is_admin": False, "linked_user_id": None}
    async with client_as(ghost) as c:
        assert (await c.post("/api/v1/push/subscribe", json=BODY)).status_code == 401
    assert await rows(test_db) == []


# ── unsubscribe ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unsubscribe_removes_the_callers_device(client_as, test_db, felix, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        assert (await c.post("/api/v1/push/unsubscribe", json={"endpoint": FCM})).status_code == 204
    assert await rows(test_db) == []


@pytest.mark.asyncio
async def test_unsubscribe_is_idempotent(client_as, felix, push_on):
    async with client_as(felix) as c:
        assert (await c.post("/api/v1/push/unsubscribe", json={"endpoint": FCM})).status_code == 204


@pytest.mark.asyncio
async def test_nobody_can_remove_another_members_device(client_as, test_db, felix, mara, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
    async with client_as(mara) as c:
        assert (await c.post("/api/v1/push/unsubscribe", json={"endpoint": FCM})).status_code == 204
    assert len(await rows(test_db)) == 1


@pytest.mark.asyncio
async def test_unsubscribing_still_works_after_push_was_switched_off(client_as, test_db, felix, push_on, monkeypatch):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        monkeypatch.setattr(settings, "vapid_private_key", "")
        assert (await c.post("/api/v1/push/unsubscribe", json={"endpoint": FCM})).status_code == 204
    assert await rows(test_db) == []


# ── test notification ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_notification_goes_to_the_callers_devices_only(client_as, felix, mara, push_on):
    async with client_as(mara) as c:
        await c.post("/api/v1/push/subscribe", json={**BODY, "endpoint": MOZ})
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        with patch("app.services.push.webpush_async", new_callable=AsyncMock) as send:
            resp = await c.post("/api/v1/push/test")
    assert resp.status_code == 200 and resp.json() == {"devices": 1, "delivered": 1}
    assert [x.kwargs["subscription_info"]["endpoint"] for x in send.call_args_list] == [FCM]


@pytest.mark.asyncio
async def test_test_notification_without_a_device_explains_what_to_do(client_as, felix, push_on):
    async with client_as(felix) as c:
        resp = await c.post("/api/v1/push/test")
    assert resp.status_code == 404
    assert "Turn notifications on" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_test_notification_reports_when_delivery_failed(client_as, felix, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        with patch("app.services.push.webpush_async", side_effect=RuntimeError("down")):
            resp = await c.post("/api/v1/push/test")
    assert resp.json() == {"devices": 1, "delivered": 0}


@pytest.mark.asyncio
async def test_test_notification_is_rate_limited(client_as, felix, push_on):
    async with client_as(felix) as c:
        await c.post("/api/v1/push/subscribe", json=BODY)
        with patch("app.services.push.webpush_async", new_callable=AsyncMock):
            codes = [(await c.post("/api/v1/push/test")).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


@pytest.mark.asyncio
async def test_test_notification_is_refused_when_push_is_off(client_as, felix, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    async with client_as(felix) as c:
        assert (await c.post("/api/v1/push/test")).status_code == 503
