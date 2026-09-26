# tests/test_invites_api.py — invite links let members create their own login and
# connect their own Spond account, so admins never handle members' Spond passwords.
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.jwt import decode_access_token
from app.core.spond_client import SpondAuthError
from app.models.frontend_user import FrontendUser
from app.models.invite import Invite
from app.models.user import User

ADMIN = {"sub": str(uuid.uuid4()), "username": "admin", "is_admin": True, "linked_user_id": None}
MEMBER = {"sub": str(uuid.uuid4()), "username": "felix", "is_admin": False, "linked_user_id": None}


def _accept_body(token: str, **overrides) -> dict:
    return {
        "token": token,
        "username": "mara",
        "password": "a-long-password",
        "spond_login": "mara@example.com",
        "spond_password": "spond-secret",
        "display_name": "Mara Lind",
        **overrides,
    }


async def _create_invite(client_as, **body) -> dict:
    async with client_as(ADMIN) as admin:
        resp = await admin.post("/api/v1/invites", json={"note": "Mara", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Admin: create / list / revoke ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_token_once_and_stores_only_its_hash(client_as, test_db):
    created = await _create_invite(client_as)
    assert created["status"] == "pending"
    assert created["note"] == "Mara"
    assert len(created["token"]) >= 40
    expires = datetime.fromisoformat(created["expires_at"])
    assert timedelta(days=6, hours=23) < _aware(expires) - datetime.now(timezone.utc) <= timedelta(days=7)

    row = (await test_db.execute(select(Invite))).scalar_one()
    assert row.token_hash == hashlib.sha256(created["token"].encode()).hexdigest()
    assert created["token"] not in row.token_hash

    async with client_as(ADMIN) as admin:
        listed = (await admin.get("/api/v1/invites")).json()
    assert [i["id"] for i in listed] == [created["id"]]
    assert "token" not in listed[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 31])
async def test_validity_is_between_one_and_thirty_days(client_as, days):
    async with client_as(ADMIN) as admin:
        resp = await admin.post("/api/v1/invites", json={"days_valid": days})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_revoke_deletes_the_invite(client_as):
    created = await _create_invite(client_as)
    async with client_as(ADMIN) as admin:
        assert (await admin.delete(f"/api/v1/invites/{created['id']}")).status_code == 204
        assert (await admin.get("/api/v1/invites")).json() == []
        assert (await admin.delete(f"/api/v1/invites/{created['id']}")).status_code == 404
    async with client_as(None) as anon:
        check = (await anon.post("/api/v1/invites/check", json={"token": created["token"]})).json()
    assert check == {"valid": False, "reason": "unknown", "note": None, "expires_at": None}


@pytest.mark.asyncio
async def test_members_cannot_manage_invites(client_as):
    async with client_as(MEMBER) as member:
        assert (await member.post("/api/v1/invites", json={})).status_code == 403
        assert (await member.get("/api/v1/invites")).status_code == 403


# ── Public: check ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_reports_valid_unknown_expired_and_used(client_as, test_db, spond_api):
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        ok = (await anon.post("/api/v1/invites/check", json={"token": created["token"]})).json()
        assert ok["valid"] is True and ok["note"] == "Mara" and ok["reason"] is None

        unknown = (await anon.post("/api/v1/invites/check", json={"token": "nope"})).json()
        assert unknown["valid"] is False and unknown["reason"] == "unknown"

        assert (await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"]))).status_code == 201
        used = (await anon.post("/api/v1/invites/check", json={"token": created["token"]})).json()
        assert used["valid"] is False and used["reason"] == "used"

    expired = await _create_invite(client_as)
    row = (await test_db.execute(select(Invite).where(Invite.id == uuid.UUID(expired["id"])))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await test_db.commit()
    async with client_as(None) as anon:
        gone = (await anon.post("/api/v1/invites/check", json={"token": expired["token"]})).json()
    assert gone["valid"] is False and gone["reason"] == "expired"


# ── Public: accept ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_creates_linked_login_and_signs_the_member_in(client_as, test_db, spond_api):
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"], is_admin=True))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "mara"
    assert body["is_admin"] is False, "an invite can never create an admin"

    spond_api.assert_awaited_once()
    assert spond_api.await_args.args[1:] == ("mara@example.com", "spond-secret")

    spond_user = (await test_db.execute(select(User))).scalar_one()
    assert spond_user.login == "mara@example.com"
    assert spond_user.display_name == "Mara Lind"
    assert spond_user.profile_id == "PROFILE-123"
    assert "spond-secret" not in spond_user.encrypted_password

    login = (await test_db.execute(select(FrontendUser))).scalar_one()
    assert login.linked_user_id == spond_user.id
    assert login.hashed_password != "a-long-password"

    invite = (await test_db.execute(select(Invite))).scalar_one()
    assert invite.used_at is not None
    assert invite.used_by_id == login.id

    claims = decode_access_token(resp.cookies["sb_session"])
    assert claims["username"] == "mara"
    assert claims["linked_user_id"] == str(spond_user.id)
    assert claims["is_admin"] is False


@pytest.mark.asyncio
async def test_display_name_defaults_to_the_login_name(client_as, test_db, spond_api):
    created = await _create_invite(client_as)
    body = _accept_body(created["token"])
    del body["display_name"]
    async with client_as(None) as anon:
        assert (await anon.post("/api/v1/invites/accept", json=body)).status_code == 201
    assert (await test_db.execute(select(User))).scalar_one().display_name == "mara"


@pytest.mark.asyncio
async def test_an_invite_works_only_once(client_as, spond_api):
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        assert (await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"]))).status_code == 201
        again = await anon.post(
            "/api/v1/invites/accept",
            json=_accept_body(created["token"], username="other", spond_login="other@example.com"),
        )
    assert again.status_code == 410
    assert "already been used" in again.json()["detail"]


@pytest.mark.asyncio
async def test_expired_invite_is_rejected_before_contacting_spond(client_as, test_db, spond_api):
    created = await _create_invite(client_as)
    row = (await test_db.execute(select(Invite))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await test_db.commit()
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"]))
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"]
    spond_api.assert_not_awaited()
    assert (await test_db.execute(select(FrontendUser))).scalars().all() == []


@pytest.mark.asyncio
async def test_taken_username_is_rejected_and_the_invite_stays_usable(client_as, test_db, spond_api):
    test_db.add(FrontendUser(username="mara", hashed_password="x", is_admin=False))
    await test_db.commit()
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"]))
        assert resp.status_code == 409
        assert "username" in resp.json()["detail"].lower()
        spond_api.assert_not_awaited()
        check = (await anon.post("/api/v1/invites/check", json={"token": created["token"]})).json()
    assert check["valid"] is True


@pytest.mark.asyncio
async def test_already_connected_spond_account_is_rejected(client_as, test_db, spond_api):
    test_db.add(User(display_name="Mara", login="mara@example.com", encrypted_password="x"))
    await test_db.commit()
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"]))
    assert resp.status_code == 409
    assert "already connected" in resp.json()["detail"]
    spond_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_spond_password_creates_nothing(client_as, test_db, spond_api):
    spond_api.side_effect = SpondAuthError("bad credentials")
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"]))
        assert resp.status_code == 401
        assert "Spond did not accept" in resp.json()["detail"]
        check = (await anon.post("/api/v1/invites/check", json={"token": created["token"]})).json()
    assert check["valid"] is True
    assert (await test_db.execute(select(User))).scalars().all() == []
    assert (await test_db.execute(select(FrontendUser))).scalars().all() == []


@pytest.mark.asyncio
async def test_short_password_is_rejected(client_as, spond_api):
    created = await _create_invite(client_as)
    async with client_as(None) as anon:
        resp = await anon.post("/api/v1/invites/accept", json=_accept_body(created["token"], password="short"))
    assert resp.status_code == 422
    spond_api.assert_not_awaited()
