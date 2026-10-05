# tests/test_account_safety.py — the admin panel must not be able to lock everyone out,
# the first admin must not get a password everybody knows, and the API schema is not public.
import pytest

from app.main import assert_admin_password_is_safe
from tests.audit_helpers import client_factory, make_login


@pytest.fixture
def client(test_db, real_sessions):
    return client_factory(test_db)


@pytest.mark.asyncio
async def test_an_admin_cannot_delete_their_own_login(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    await make_login(test_db, "second", admin=True)
    async with client(boss) as c:
        resp = await c.delete(f"/api/v1/accounts/{boss.id}")
    assert resp.status_code == 400 and "own login" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_the_last_administrator_cannot_be_deleted_or_demoted(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    member = await make_login(test_db, "felix")
    async with client(boss) as c:
        assert (await c.patch(f"/api/v1/accounts/{boss.id}", json={"is_admin": False})).status_code == 409
        # a second admin, deleted by the first, is fine; the first remains
        other = await make_login(test_db, "second", admin=True)
        assert (await c.delete(f"/api/v1/accounts/{other.id}")).status_code == 204
        assert (await c.get("/api/v1/accounts")).status_code == 200
        assert (await c.delete(f"/api/v1/accounts/{member.id}")).status_code == 204


@pytest.mark.asyncio
async def test_with_two_admins_one_can_demote_the_other(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    other = await make_login(test_db, "second", admin=True)
    async with client(boss) as c:
        assert (await c.patch(f"/api/v1/accounts/{other.id}", json={"is_admin": False})).status_code == 200
        assert (await c.patch(f"/api/v1/accounts/{boss.id}", json={"is_admin": False})).status_code == 409


@pytest.mark.asyncio
async def test_demoting_or_deleting_a_member_is_never_blocked(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    member = await make_login(test_db, "felix")
    async with client(boss) as c:
        assert (await c.patch(f"/api/v1/accounts/{member.id}", json={"is_admin": False})).status_code == 200
        assert (await c.delete(f"/api/v1/accounts/{member.id}")).status_code == 204


@pytest.mark.parametrize("password", ["changeme", "changeme_admin_password", "CHANGEME", "password", "admin123", "", "short", "123456789"])
def test_the_first_admin_never_gets_an_example_or_trivial_password(password):
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        assert_admin_password_is_safe(password)


@pytest.mark.parametrize("password", ["correct-horse-battery", "x7Kp-2mQ9-vLw", "a" * 10])
def test_a_real_password_is_accepted(password):
    assert_admin_password_is_safe(password)


@pytest.mark.asyncio
async def test_the_api_schema_is_for_admins_only(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    member = await make_login(test_db, "felix")
    async with client(None) as anon:
        assert (await anon.get("/openapi.json")).status_code == 401
    async with client(member) as c:
        assert (await c.get("/openapi.json")).status_code == 403
    async with client(boss) as c:
        resp = await c.get("/openapi.json")
        assert resp.status_code == 200 and resp.json()["info"]["title"] == "Spond Multi-User Bot"
        assert "/api/v1/auth/login" in resp.json()["paths"]
        assert (await c.get("/docs")).status_code == 200


@pytest.mark.asyncio
async def test_the_docs_pages_still_point_at_the_protected_schema(client, test_db):
    boss = await make_login(test_db, "boss", admin=True)
    async with client(boss) as c:
        assert "/openapi.json" in (await c.get("/docs")).text
        assert "/openapi.json" in (await c.get("/redoc")).text
