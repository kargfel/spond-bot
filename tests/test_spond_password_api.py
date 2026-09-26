# tests/test_spond_password_api.py — PUT /api/v1/spond-accounts/{id}/password replaces
# a stored Spond password after the member changed it in Spond. The new password is
# verified with Spond first; nothing is stored if Spond rejects it.
import uuid

import pytest
from sqlalchemy import select

from app.core.security import decrypt, encrypt
from app.core.spond_client import SpondAuthError
from app.models.user import User

ADMIN = {"sub": str(uuid.uuid4()), "username": "admin", "is_admin": True, "linked_user_id": None}


async def _account(test_db, *, profile_id="PROFILE-123") -> User:
    user = User(
        id=uuid.uuid4(),
        display_name="Felix",
        login="felix@example.com",
        encrypted_password=encrypt("old-password"),
        encrypted_access_token=encrypt("old-token"),
        profile_id=profile_id,
    )
    test_db.add(user)
    await test_db.commit()
    return user


def _member_of(user: User) -> dict:
    return {"sub": str(uuid.uuid4()), "username": "felix", "is_admin": False, "linked_user_id": str(user.id)}


async def _stored(test_db, user_id) -> User:
    test_db.expire_all()
    return (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()


@pytest.mark.asyncio
async def test_admin_replaces_the_password_after_spond_accepts_it(client_as, test_db, spond_api):
    user = await _account(test_db)
    async with client_as(ADMIN) as admin:
        resp = await admin.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "new-password"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(user.id)
    assert "password" not in str(body).lower()

    assert spond_api.await_args.args[1:] == ("felix@example.com", "new-password")
    stored = await _stored(test_db, user.id)
    assert decrypt(stored.encrypted_password) == "new-password"
    assert decrypt(stored.encrypted_access_token) == "spond-token"
    assert stored.token_acquired_at is not None


@pytest.mark.asyncio
async def test_member_can_update_their_own_account(client_as, test_db, spond_api):
    user = await _account(test_db)
    async with client_as(_member_of(user)) as member:
        resp = await member.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "new-password"})
    assert resp.status_code == 200
    assert decrypt((await _stored(test_db, user.id)).encrypted_password) == "new-password"


@pytest.mark.asyncio
async def test_member_cannot_touch_someone_elses_account(client_as, test_db, spond_api):
    user = await _account(test_db)
    stranger = {"sub": str(uuid.uuid4()), "username": "x", "is_admin": False, "linked_user_id": str(uuid.uuid4())}
    async with client_as(stranger) as client:
        resp = await client.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "new-password"})
    assert resp.status_code == 403
    spond_api.assert_not_awaited()
    assert decrypt((await _stored(test_db, user.id)).encrypted_password) == "old-password"


@pytest.mark.asyncio
async def test_rejected_password_changes_nothing(client_as, test_db, spond_api):
    spond_api.side_effect = SpondAuthError("bad credentials")
    user = await _account(test_db)
    async with client_as(ADMIN) as admin:
        resp = await admin.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "typo"})
    assert resp.status_code == 401
    assert "Spond did not accept" in resp.json()["detail"]
    stored = await _stored(test_db, user.id)
    assert decrypt(stored.encrypted_password) == "old-password"
    assert decrypt(stored.encrypted_access_token) == "old-token"


@pytest.mark.asyncio
async def test_login_now_belonging_to_another_profile_is_refused(client_as, test_db, spond_api):
    user = await _account(test_db, profile_id="SOMEONE-ELSE")
    async with client_as(ADMIN) as admin:
        resp = await admin.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "new-password"})
    assert resp.status_code == 409
    assert "different Spond profile" in resp.json()["detail"]
    assert decrypt((await _stored(test_db, user.id)).encrypted_password) == "old-password"


@pytest.mark.asyncio
async def test_spond_unreachable_is_reported_and_changes_nothing(client_as, test_db, spond_api):
    spond_api.side_effect = OSError("network down")
    user = await _account(test_db)
    async with client_as(ADMIN) as admin:
        resp = await admin.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "new-password"})
    assert resp.status_code == 503
    assert decrypt((await _stored(test_db, user.id)).encrypted_password) == "old-password"


@pytest.mark.asyncio
async def test_unknown_account_is_404(client_as, spond_api):
    async with client_as(ADMIN) as admin:
        resp = await admin.put(f"/api/v1/spond-accounts/{uuid.uuid4()}/password", json={"password": "new-password"})
    assert resp.status_code == 404
    spond_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_password_is_rejected(client_as, test_db, spond_api):
    user = await _account(test_db)
    async with client_as(ADMIN) as admin:
        resp = await admin.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": ""})
    assert resp.status_code == 422
    spond_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_requires_a_session(client_as, test_db, spond_api):
    user = await _account(test_db)
    async with client_as(None) as anon:
        resp = await anon.put(f"/api/v1/spond-accounts/{user.id}/password", json={"password": "new-password"})
    assert resp.status_code == 401
