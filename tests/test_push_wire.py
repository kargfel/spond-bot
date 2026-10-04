# tests/test_push_wire.py — what actually goes over the wire. pywebpush is NOT mocked here:
# a local server plays the push service, and the test plays the browser — it decrypts the
# message with its own keys and checks the VAPID signature. Proves the generated key, the
# signing and the payload encryption work together.
import base64
import json
import os
import re
import time
import uuid

import http_ece
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app.config import settings
from app.models.frontend_user import FrontendUser
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import push

b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731


@pytest.fixture
async def push_service():
    """A fake push service that records what it receives and answers with a chosen status."""
    received = []
    behaviour = {"status": 201}

    async def handler(request: web.Request):
        received.append({
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "body": await request.read(),
            "path": request.path,
        })
        return web.Response(status=behaviour["status"])

    app = web.Application()
    app.router.add_post("/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    yield server, received, behaviour
    await server.close()


@pytest.fixture
def browser_keys():
    """What a browser holds for one subscription: an EC key pair and a 16-byte auth secret."""
    private = ec.generate_private_key(ec.SECP256R1())
    point = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return private, b64(point), auth, b64(auth)


@pytest.fixture
def vapid_key(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    key = generate()
    monkeypatch.setattr(settings, "vapid_private_key", key)
    monkeypatch.setattr(settings, "vapid_subject", "mailto:ops@example.com")
    return key


async def member_with_device(db, endpoint, p256dh, auth):
    spond = User(id=uuid.uuid4(), display_name="Felix", login="f@example.com", encrypted_password="x")
    login = FrontendUser(id=uuid.uuid4(), username="felix", hashed_password="x", linked_user_id=spond.id)
    db.add_all([spond, login])
    await db.flush()
    db.add(PushSubscription(frontend_user_id=login.id, endpoint=endpoint, p256dh=p256dh, auth=auth))
    await db.commit()
    return spond, login


@pytest.mark.asyncio
async def test_the_browser_can_decrypt_the_message(test_db, push_service, browser_keys, vapid_key):
    server, received, _ = push_service
    private, p256dh, auth_raw, auth = browser_keys
    spond, _ = await member_with_device(test_db, str(server.make_url("/wpush/abc")), p256dh, auth)

    payload = push.build_rsvp_payload("e1", "Training, Hall B — Ärger & Übung", "accept", "success")
    devices, delivered = await push.send_to_spond_user(test_db, spond.id, payload)

    assert (devices, delivered) == (1, 1)
    (req,) = received
    assert req["path"] == "/wpush/abc"
    assert req["headers"]["content-encoding"] == "aes128gcm"
    assert req["headers"]["ttl"] == str(push.TTL_SECONDS)
    assert req["headers"]["urgency"] == "high"
    plain = http_ece.decrypt(req["body"], private_key=private, auth_secret=auth_raw, version="aes128gcm")
    assert json.loads(plain.decode()) == payload


@pytest.mark.asyncio
async def test_the_message_is_signed_with_the_servers_vapid_key(test_db, push_service, browser_keys, vapid_key):
    server, received, _ = push_service
    _, p256dh, _, auth = browser_keys
    spond, _ = await member_with_device(test_db, str(server.make_url("/wpush/abc")), p256dh, auth)

    await push.send_to_spond_user(test_db, spond.id, {"title": "x"})

    authorization = received[0]["headers"]["authorization"]
    token, key = re.fullmatch(r"vapid t=([^,]+),k=(.+)", authorization).groups()
    # The key the browser checks against is the one the dashboard hands out
    assert key == push.public_key()

    # Verify the ES256 signature independently of the library that made it
    header_b64, claims_b64, signature_b64 = token.split(".")
    pad = lambda t: t + "=" * (-len(t) % 4)  # noqa: E731
    raw = base64.urlsafe_b64decode(pad(signature_b64))
    assert len(raw) == 64
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.urlsafe_b64decode(pad(key)))
    public.verify(
        utils.encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{header_b64}.{claims_b64}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )  # raises InvalidSignature on a forged or corrupted token

    claims = json.loads(base64.urlsafe_b64decode(pad(claims_b64)))
    assert claims["sub"] == "mailto:ops@example.com"
    assert claims["aud"] == f"http://{server.host}:{server.port}"
    assert claims["exp"] > time.time()
    assert claims["exp"] - time.time() <= 24 * 3600  # push services reject tokens valid for longer


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_a_real_gone_response_removes_the_device(test_db, push_service, browser_keys, vapid_key, status):
    server, _, behaviour = push_service
    behaviour["status"] = status
    _, p256dh, _, auth = browser_keys
    spond, _ = await member_with_device(test_db, str(server.make_url("/wpush/abc")), p256dh, auth)

    assert await push.send_to_spond_user(test_db, spond.id, {"title": "x"}) == (1, 0)

    from sqlalchemy import select

    assert (await test_db.execute(select(PushSubscription))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_server_error_keeps_the_device(test_db, push_service, browser_keys, vapid_key):
    server, _, behaviour = push_service
    behaviour["status"] = 503
    _, p256dh, _, auth = browser_keys
    spond, _ = await member_with_device(test_db, str(server.make_url("/wpush/abc")), p256dh, auth)

    assert await push.send_to_spond_user(test_db, spond.id, {"title": "x"}) == (1, 0)

    from sqlalchemy import select

    assert len((await test_db.execute(select(PushSubscription))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_an_unreachable_push_service_does_not_raise(test_db, browser_keys, vapid_key):
    _, p256dh, _, auth = browser_keys
    spond, _ = await member_with_device(test_db, "http://127.0.0.1:9/wpush/abc", p256dh, auth)
    assert await push.send_to_spond_user(test_db, spond.id, {"title": "x"}) == (1, 0)


@pytest.mark.asyncio
async def test_a_device_with_broken_keys_does_not_stop_the_others(test_db, push_service, browser_keys, vapid_key):
    server, received, _ = push_service
    private, p256dh, auth_raw, auth = browser_keys
    spond, login = await member_with_device(test_db, str(server.make_url("/wpush/good")), p256dh, auth)
    test_db.add(PushSubscription(frontend_user_id=login.id, endpoint=str(server.make_url("/wpush/bad")), p256dh="garbage", auth="x"))
    await test_db.commit()

    devices, delivered = await push.send_to_spond_user(test_db, spond.id, {"title": "x"})

    assert (devices, delivered) == (2, 1)
    assert [r["path"] for r in received] == ["/wpush/good"]
