# tests/test_self_connect_api.py — a signed-in login without a Spond account can
# connect one itself: POST /api/v1/spond-accounts/me.
import uuid

import pytest
from sqlalchemy import select

from app.core.jwt import decode_access_token
from app.core.spond_client import SpondAuthError
from app.models.frontend_user import FrontendUser
from app.models.user import User

BODY = {"login": "felix@example.com", "password": "spond-secret", "display_name": "Felix Karg"}


async def _login(test_db, *, is_admin=False, linked_user_id=None) -> dict:
    fu = FrontendUser(id=uuid.uuid4(), username="felix", hashed_password="x",
                      is_admin=is_admin, linked_user_id=linked_user_id)
    test_db.add(fu)
    await test_db.commit()
    # The session claims deliberately say "not linked" even when the DB says otherwise:
    # the endpoint must trust the database, not a possibly stale cookie.
    return {"sub": str(fu.id), "username": "felix", "is_admin": is_admin, "linked_user_id": None}


@pytest.mark.asyncio
async def test_member_connects_own_account_and_gets_a_fresh_session(client_as, test_db, spond_api):
    claims = await _login(test_db)
    async with client_as(claims) as client:
        resp = await client.post("/api/v1/spond-accounts/me", json=BODY)
    assert resp.status_code == 201, resp.text
    assert resp.json()["login"] == "felix@example.com"
    assert resp.json()["display_name"] == "Felix Karg"

    spond_user = (await test_db.execute(select(User))).scalar_one()
    login = (await test_db.execute(select(FrontendUser))).scalar_one()
    assert login.linked_user_id == spond_user.id
    assert "spond-secret" not in spond_user.encrypted_password

    new_claims = decode_access_token(resp.cookies["sb_session"])
    assert new_claims["linked_user_id"] == str(spond_user.id)
    assert new_claims["sub"] == claims["sub"]
    assert new_claims["is_admin"] is False


@pytest.mark.asyncio
async def test_admin_without_account_can_connect_one_and_stays_admin(client_as, test_db, spond_api):
    claims = await _login(test_db, is_admin=True)
    async with client_as(claims) as client:
        resp = await client.post("/api/v1/spond-accounts/me", json=BODY)
    assert resp.status_code == 201
    assert decode_access_token(resp.cookies["sb_session"])["is_admin"] is True


@pytest.mark.asyncio
async def test_already_linked_login_is_rejected_without_contacting_spond(client_as, test_db, spond_api):
    existing = User(display_name="Old", login="old@example.com", encrypted_password="x")
    test_db.add(existing)
    await test_db.commit()
    claims = await _login(test_db, linked_user_id=existing.id)
    async with client_as(claims) as client:
        resp = await client.post("/api/v1/spond-accounts/me", json=BODY)
    assert resp.status_code == 409
    assert "already linked" in resp.json()["detail"]
    spond_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_spond_account_used_by_someone_else_is_rejected(client_as, test_db, spond_api):
    test_db.add(User(display_name="Felix", login="felix@example.com", encrypted_password="x"))
    await test_db.commit()
    claims = await _login(test_db)
    async with client_as(claims) as client:
        resp = await client.post("/api/v1/spond-accounts/me", json=BODY)
    assert resp.status_code == 409
    spond_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_spond_password_leaves_the_login_unlinked(client_as, test_db, spond_api):
    spond_api.side_effect = SpondAuthError("bad credentials")
    claims = await _login(test_db)
    async with client_as(claims) as client:
        resp = await client.post("/api/v1/spond-accounts/me", json=BODY)
    assert resp.status_code == 401
    assert (await test_db.execute(select(FrontendUser))).scalar_one().linked_user_id is None
    assert (await test_db.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_requires_a_session(client_as, spond_api):
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/spond-accounts/me", json=BODY)
    assert resp.status_code == 401
