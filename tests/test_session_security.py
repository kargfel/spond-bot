# tests/test_session_security.py — a session is a claim about who someone was when they signed in.
# What a login may do is read from the database on every request, so demoting, deleting or
# changing the password of a login takes effect at once instead of when the cookie runs out.
import inspect
import uuid
from datetime import timedelta

import pytest
from joserfc import jwt as jose_jwt

from app.api import deps
from app.core import jwt as app_jwt
from app.core.jwt import ACCESS_TOKEN_TTL, create_access_token
from app.core.security import password_version
from app.models.event import Event
from app.models.user import User
from tests.audit_helpers import PASSWORD, client_factory, cookie_for, make_login


@pytest.fixture
def client(test_db, real_sessions):
    return client_factory(test_db)


# ── demotion, deletion ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_demoted_admin_loses_admin_rights_immediately(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    async with client(boss) as c:
        assert (await c.get("/api/v1/accounts")).status_code == 200
        boss.is_admin = False
        await test_db.commit()
        assert (await c.get("/api/v1/accounts")).status_code == 403
        assert (await c.get("/api/v1/admin/stats")).status_code == 403
        assert (await c.get("/docs")).status_code == 403


@pytest.mark.asyncio
async def test_a_deleted_login_is_locked_out_immediately(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    async with client(boss) as c:
        assert (await c.get("/api/v1/events")).status_code == 200
        await test_db.delete(boss)
        await test_db.commit()
        for path in ("/api/v1/events", "/api/v1/accounts", "/api/v1/push/config", "/api/v1/admin/audit"):
            resp = await c.get(path)
            assert resp.status_code == 401, path
        assert resp.json()["detail"] == "This login no longer exists."


@pytest.mark.asyncio
async def test_a_promoted_member_needs_no_new_login_to_become_admin_but_a_demoted_one_cannot_keep_it(client, test_db):
    member = await make_login(test_db, "felix")
    async with client(member) as c:
        assert (await c.get("/api/v1/accounts")).status_code == 403
        member.is_admin = True
        await test_db.commit()
        assert (await c.get("/api/v1/accounts")).status_code == 200


@pytest.mark.asyncio
async def test_the_linked_spond_account_is_always_the_current_one(client, test_db):
    a = User(id=uuid.uuid4(), display_name="A", login="a@x.com", encrypted_password="x")
    b = User(id=uuid.uuid4(), display_name="B", login="b@x.com", encrypted_password="x")
    test_db.add_all([a, b])
    await test_db.commit()
    test_db.add_all([Event(id=uuid.uuid4(), spond_event_id="EA", user_id=a.id, heading="A's event"),
                     Event(id=uuid.uuid4(), spond_event_id="EB", user_id=b.id, heading="B's event")])
    member = await make_login(test_db, "felix", linked_user_id=a.id)
    async with client(member) as c:
        assert [e["heading"] for e in (await c.get("/api/v1/events")).json()] == ["A's event"]
        member.linked_user_id = b.id  # an admin re-links the login
        await test_db.commit()
        assert [e["heading"] for e in (await c.get("/api/v1/events")).json()] == ["B's event"]


# ── password changes end sessions ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_changing_the_password_ends_every_other_session_but_not_this_one(client, test_db):
    felix = await make_login(test_db, "felix")
    stolen = cookie_for(felix)
    async with client(felix) as mine:
        resp = await mine.patch("/api/v1/auth/me/password", json={"current_password": PASSWORD, "new_password": "brand-new-password"})
        assert resp.status_code == 204
        assert "sb_session=" in resp.headers["set-cookie"] and "HttpOnly" in resp.headers["set-cookie"]
        assert (await mine.get("/api/v1/events")).status_code == 200, "the session that changed it carries on"
    async with client(None) as thief:
        thief.cookies.set("sb_session", stolen)
        denied = await thief.get("/api/v1/events")
    assert denied.status_code == 401 and "password was changed" in denied.json()["detail"]


@pytest.mark.asyncio
async def test_an_admin_resetting_a_members_password_ends_the_members_sessions(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    felix = await make_login(test_db, "felix")
    async with client(felix) as member, client(boss) as admin:
        assert (await member.get("/api/v1/events")).status_code == 200
        assert (await admin.patch(f"/api/v1/accounts/{felix.id}", json={"new_password": "reset-by-admin-1"})).status_code == 200
        assert (await member.get("/api/v1/events")).status_code == 401
        assert (await admin.get("/api/v1/accounts")).status_code == 200


@pytest.mark.asyncio
async def test_signing_in_again_after_a_reset_works(client, test_db):
    felix = await make_login(test_db, "felix")
    felix.hashed_password = (await make_login(test_db, "other", password="fresh-password-1")).hashed_password
    await test_db.commit()
    async with client(None) as c:
        assert (await c.post("/api/v1/auth/login", json={"username": "felix", "password": "fresh-password-1"})).status_code == 204
        assert (await c.get("/api/v1/events")).status_code == 200


@pytest.mark.asyncio
async def test_a_forged_password_version_is_rejected(client, test_db):
    felix = await make_login(test_db, "felix")
    token = create_access_token({"sub": str(felix.id), "username": "felix", "is_admin": True, "linked_user_id": None, "pwv": "0" * 16})
    async with client(None) as c:
        c.cookies.set("sb_session", token)
        assert (await c.get("/api/v1/accounts")).status_code == 401


@pytest.mark.asyncio
async def test_sessions_from_before_this_check_existed_still_work_but_are_still_checked(client, test_db):
    felix = await make_login(test_db, "felix")
    old = create_access_token({"sub": str(felix.id), "username": "felix", "is_admin": True, "linked_user_id": None})  # no pwv, claims admin
    async with client(None) as c:
        c.cookies.set("sb_session", old)
        assert (await c.get("/api/v1/events")).status_code == 200
        assert (await c.get("/api/v1/accounts")).status_code == 403, "the claim says admin, the database says member"


@pytest.mark.asyncio
async def test_the_wrong_current_password_changes_nothing(client, test_db):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        resp = await c.patch("/api/v1/auth/me/password", json={"current_password": "not-my-password", "new_password": "brand-new-password"})
        assert resp.status_code == 401 and "set-cookie" not in resp.headers
        assert (await c.get("/api/v1/events")).status_code == 200


@pytest.mark.asyncio
async def test_password_changes_are_rate_limited(client, test_db):
    felix = await make_login(test_db, "felix")
    async with client(felix) as c:
        codes = [(await c.patch("/api/v1/auth/me/password", json={"current_password": f"guess-{i}-guess", "new_password": "brand-new-password"})).status_code
                 for i in range(7)]
    assert codes == [401] * 5 + [429] * 2


def test_the_password_fingerprint_changes_with_the_password_and_leaks_nothing():
    a, b = password_version("$2b$12$aaaa"), password_version("$2b$12$bbbb")
    assert a != b and len(a) == 16 and password_version("$2b$12$aaaa") == a
    assert "aaaa" not in a


# ── malformed and unsigned tokens ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [{"sub": "not-a-uuid"}, {"sub": None}, {}, {"sub": 42}])
async def test_tokens_without_a_usable_subject_are_rejected(client, test_db, claims):
    token = create_access_token({"username": "x", "is_admin": True, **claims})
    async with client(None) as c:
        c.cookies.set("sb_session", token)
        assert (await c.get("/api/v1/accounts")).status_code == 401


@pytest.mark.asyncio
async def test_a_token_without_an_expiry_is_refused(client, test_db):
    felix = await make_login(test_db, "felix")
    never_ends = jose_jwt.encode({"alg": "HS256"}, {"sub": str(felix.id), "username": "felix", "is_admin": False}, app_jwt._KEY)
    async with client(None) as c:
        c.cookies.set("sb_session", never_ends)
        assert (await c.get("/api/v1/events")).status_code == 401


@pytest.mark.asyncio
async def test_expired_and_foreign_tokens_are_refused(client, test_db):
    from datetime import datetime, timezone

    from joserfc.jwk import OctKey

    felix = await make_login(test_db, "felix")
    base = {"sub": str(felix.id), "username": "felix", "is_admin": True, "linked_user_id": None}
    expired = jose_jwt.encode({"alg": "HS256"}, {**base, "exp": datetime.now(timezone.utc) - timedelta(seconds=5)}, app_jwt._KEY)
    foreign = jose_jwt.encode({"alg": "HS256"}, {**base, "exp": datetime.now(timezone.utc) + ACCESS_TOKEN_TTL},
                              OctKey.import_key(b"an-attacker-chose-this-key-entirely"))
    none_alg = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0." + "eyJzdWIiOiJ4IiwiaXNfYWRtaW4iOnRydWV9" + "."
    async with client(None) as c:
        for token in (expired, foreign, none_alg, "garbage"):
            c.cookies.set("sb_session", token)
            assert (await c.get("/api/v1/accounts")).status_code == 401, token[:20]


# ── streams must not pin a database connection ────────────────────────────


def test_the_login_check_uses_its_own_short_session_not_the_requests():
    """SSE streams stay open for hours; a request-scoped session would hold a pool connection for each."""
    from app.api import stream

    assert "db" not in inspect.signature(deps._get_current_user).parameters
    routes = [r for r in stream.router.routes if r.path.endswith("/stream")]
    assert {r.path for r in routes} == {"/admin/stream", "/user/stream"}

    def calls(dependant):
        yield dependant.call
        for d in dependant.dependencies:
            yield from calls(d)

    for route in routes:
        assert deps.get_db not in set(calls(route.dependant)), route.path
