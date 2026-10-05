# tests/test_audit_events.py — every kind of change leaves a row saying who did it, to what,
# and how it ended. Runs through the real HTTP stack with real session cookies.
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.security import encrypt
from app.core.spond_client import SpondAuthError
from app.models.event import Event
from app.models.user import User
from tests.audit_helpers import PASSWORD, client_factory, make_login, only, rows


@pytest.fixture
async def world(audit_on, test_db):
    admin = await make_login(test_db, "admin", admin=True)
    spond = User(id=uuid.uuid4(), display_name="Felix Karg", login="felix@example.com",
                 encrypted_password=encrypt("old-spond-pw"), encrypted_access_token=encrypt("t"), profile_id="PROFILE-123")
    test_db.add(spond)
    await test_db.commit()
    felix = await make_login(test_db, "felix", linked_user_id=spond.id)
    return type("World", (), {"admin": admin, "felix": felix, "spond": spond, "db": test_db, "client": client_factory(test_db)})


def actor_is(row, login):
    assert (row.actor_type, row.actor_id, row.actor_username, row.actor_is_admin) == ("user", login.id, login.username, login.is_admin)


# ── sign-in ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signing_in_is_recorded_for_the_person_who_signed_in(world):
    async with world.client(None, ip="198.51.100.5") as c:
        assert (await c.post("/api/v1/auth/login", json={"username": "felix", "password": PASSWORD})).status_code == 204
    row = await only(world.db, "auth.login.success")
    actor_is(row, world.felix)
    assert (row.outcome, row.target_label, row.ip, row.status_code) == ("success", "felix", "198.51.100.5", 204)
    assert PASSWORD not in str(row.__dict__)


@pytest.mark.asyncio
async def test_changing_your_own_password_is_recorded_without_the_password(world):
    async with world.client(world.felix) as c:
        bad = await c.patch("/api/v1/auth/me/password", json={"current_password": "nope-nope-nope", "new_password": "brand-new-password"})
        ok = await c.patch("/api/v1/auth/me/password", json={"current_password": PASSWORD, "new_password": "brand-new-password"})
    assert (bad.status_code, ok.status_code) == (401, 204)
    denied, done = await rows(world.db, action="auth.password_changed")
    assert (denied.outcome, denied.details) == ("denied", {"reason": "wrong_current_password"})
    assert done.outcome == "success"
    actor_is(done, world.felix)
    assert "brand-new-password" not in str([r.__dict__ for r in await rows(world.db)])


# ── accounts (admin) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_admin_creating_changing_and_deleting_a_login_is_recorded(world):
    async with world.client(world.admin) as c:
        created = (await c.post("/api/v1/accounts", json={"username": "mara", "password": "long-enough-1", "is_admin": False})).json()
        await c.patch(f"/api/v1/accounts/{created['id']}", json={"is_admin": True, "new_password": "another-long-one"})
        await c.patch(f"/api/v1/accounts/{created['id']}", json={"is_admin": True})
        assert (await c.delete(f"/api/v1/accounts/{created['id']}")).status_code == 204

    made = await only(world.db, "account.created")
    actor_is(made, world.admin)
    assert (made.target_type, made.target_id, made.target_label) == ("login", created["id"], "mara")
    assert made.details["is_admin"] is False

    first, second = await rows(world.db, action="account.updated")
    assert first.details == {"is_admin": {"from": False, "to": True}, "password_reset": True}
    assert second.details is None, "an update that changes nothing has no details"
    gone = await only(world.db, "account.deleted")
    assert (gone.target_label, gone.target_id, gone.details) == ("mara", created["id"], {"was_admin": True})
    assert "long-enough-1" not in str([r.__dict__ for r in await rows(world.db)])
    assert "another-long-one" not in str([r.__dict__ for r in await rows(world.db)])


@pytest.mark.asyncio
async def test_a_member_trying_to_manage_accounts_is_recorded_as_denied(world):
    async with world.client(world.felix) as c:
        assert (await c.post("/api/v1/accounts", json={"username": "x", "password": "long-enough-1"})).status_code == 403
        assert (await c.delete(f"/api/v1/accounts/{world.admin.id}")).status_code == 403
    denied = await rows(world.db)
    assert [(r.action, r.outcome, r.status_code) for r in denied] == [("http.post", "denied", 403), ("http.delete", "denied", 403)]
    assert all(r.actor_username == "felix" for r in denied)


# ── Spond accounts ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registering_a_spond_account_never_stores_its_password(world, spond_api):
    async with world.client(world.admin) as c:
        resp = await c.post("/api/v1/spond-accounts", json={"login": "mara@example.com", "password": "spond-secret-1", "display_name": "Mara"})
    assert resp.status_code == 201
    row = await only(world.db, "spond_account.created")
    actor_is(row, world.admin)
    assert (row.target_label, row.details) == ("Mara", {"login": "mara@example.com"})
    assert "spond-secret-1" not in str([r.__dict__ for r in await rows(world.db)])


@pytest.mark.asyncio
async def test_connecting_your_own_spond_account_is_recorded(world, spond_api):
    mara = await make_login(world.db, "mara")
    async with world.client(mara) as c:
        resp = await c.post("/api/v1/spond-accounts/me", json={"login": "mara@example.com", "password": "spond-secret-1", "display_name": "Mara Lind"})
    assert resp.status_code == 201
    row = await only(world.db, "spond_account.connected")
    actor_is(row, mara)
    assert row.target_label == "Mara Lind"


@pytest.mark.asyncio
async def test_renaming_and_pausing_records_what_changed(world):
    async with world.client(world.felix) as c:
        await c.patch(f"/api/v1/spond-accounts/{world.spond.id}", json={"display_name": "Felix K."})
    async with world.client(world.admin) as c:
        await c.patch(f"/api/v1/spond-accounts/{world.spond.id}", json={"is_active": False})
    rename, pause = await rows(world.db, action="spond_account.updated")
    actor_is(rename, world.felix)
    assert rename.details == {"display_name": {"from": "Felix Karg", "to": "Felix K."}}
    actor_is(pause, world.admin)
    assert pause.details == {"is_active": {"from": True, "to": False}}


@pytest.mark.asyncio
async def test_replacing_a_spond_password_is_recorded_whether_it_worked_or_not(world, spond_api):
    async with world.client(world.felix) as c:
        spond_api.side_effect = SpondAuthError("rejected")
        bad = await c.put(f"/api/v1/spond-accounts/{world.spond.id}/password", json={"password": "wrong-new-pw"})
        spond_api.side_effect = None
        ok = await c.put(f"/api/v1/spond-accounts/{world.spond.id}/password", json={"password": "right-new-pw"})
    assert (bad.status_code, ok.status_code) == (401, 200)
    denied, done = await rows(world.db, action="spond_account.password_updated")
    assert (denied.outcome, denied.details["reason"]) == ("denied", "spond_rejected_credentials")
    assert done.outcome == "success"
    actor_is(done, world.felix)
    assert "wrong-new-pw" not in str([r.__dict__ for r in await rows(world.db)])
    assert "right-new-pw" not in str([r.__dict__ for r in await rows(world.db)])


@pytest.mark.asyncio
async def test_deleting_a_spond_account_is_recorded_with_who_it_was(world):
    async with world.client(world.admin) as c:
        assert (await c.delete(f"/api/v1/spond-accounts/{world.spond.id}")).status_code == 204
    row = await only(world.db, "spond_account.deleted")
    assert (row.target_label, row.target_id, row.details) == ("Felix Karg", str(world.spond.id), {"login": "felix@example.com"})


# ── Invites ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_invite_lifecycle_is_recorded_and_the_token_never_is(world, spond_api):
    async with world.client(world.admin) as c:
        invite = (await c.post("/api/v1/invites", json={"note": "Jonas", "days_valid": 3})).json()
    created = await only(world.db, "invite.created")
    actor_is(created, world.admin)
    assert (created.target_label, created.details["days_valid"]) == ("Jonas", 3)

    async with world.client(None, ip="198.51.100.30") as c:
        resp = await c.post("/api/v1/invites/accept", json={
            "token": invite["token"], "username": "jonas", "password": "long-enough-1",
            "spond_login": "jonas@example.com", "spond_password": "spond-secret-1", "display_name": "Jonas"})
    assert resp.status_code == 201
    joined = await only(world.db, "invite.accepted")
    assert (joined.actor_type, joined.actor_username, joined.ip) == ("user", "jonas", "198.51.100.30")
    assert joined.details == {"username": "jonas", "spond_login": "jonas@example.com"}

    async with world.client(world.admin) as c:
        spare = (await c.post("/api/v1/invites", json={"note": "Spare"})).json()
        await c.delete(f"/api/v1/invites/{spare['id']}")
    revoked = await only(world.db, "invite.revoked")
    assert (revoked.target_label, revoked.details) == ("Spare", {"status_was": "pending"})

    dump = str([r.__dict__ for r in await rows(world.db)])
    for secret in (invite["token"], spare["token"], "long-enough-1", "spond-secret-1"):
        assert secret not in dump


@pytest.mark.asyncio
async def test_failed_signups_say_why(world, spond_api):
    async with world.client(world.admin) as c:
        invite = (await c.post("/api/v1/invites", json={"note": "Mara"})).json()
    body = {"token": invite["token"], "username": "felix", "password": "long-enough-1",
            "spond_login": "mara@example.com", "spond_password": "spond-secret-1", "display_name": "Mara"}
    async with world.client(None) as c:
        await c.post("/api/v1/invites/accept", json={**body, "token": "x" * 43})            # unknown invite
        await c.post("/api/v1/invites/accept", json=body)                                    # name taken
        spond_api.side_effect = SpondAuthError("rejected")
        await c.post("/api/v1/invites/accept", json={**body, "username": "mara"})            # Spond says no
    reasons = [r.details["reason"] for r in await rows(world.db, action="invite.accept_failed")]
    assert reasons == ["invite_unknown", "username_taken", "spond_rejected"]
    assert all(r.outcome == "denied" and r.actor_type == "anonymous" for r in await rows(world.db, action="invite.accept_failed"))
    assert "x" * 43 not in str([r.__dict__ for r in await rows(world.db)])


# ── Events ────────────────────────────────────────────────────────────────


async def _event(world, choice="manual", status="pending"):
    ev = Event(id=uuid.uuid4(), spond_event_id="SP-1", user_id=world.spond.id, heading="Training, Hall B",
               user_choice=choice, status=status, invite_time=datetime(2030, 1, 1),
               created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    world.db.add(ev)
    await world.db.commit()
    return ev


@pytest.mark.asyncio
async def test_choosing_an_answer_records_before_and_after(world):
    ev = await _event(world)
    async with world.client(world.felix) as c:
        assert (await c.patch(f"/api/v1/events/{ev.id}", json={"user_choice": "accept"})).status_code == 200
        assert (await c.patch(f"/api/v1/events/{ev.id}", json={"user_choice": "decline"})).status_code == 200
    first, second = await rows(world.db, action="event.choice_set")
    actor_is(first, world.felix)
    assert (first.target_type, first.target_id, first.target_label) == ("event", str(ev.id), "Training, Hall B")
    assert first.details == {"from": "manual", "to": "accept", "owner_user_id": str(world.spond.id), "status": "pending"}
    assert (second.details["from"], second.details["to"]) == ("accept", "decline")


@pytest.mark.asyncio
async def test_an_admin_changing_someone_elses_answer_is_attributed_to_the_admin(world):
    ev = await _event(world)
    async with world.client(world.admin) as c:
        await c.patch(f"/api/v1/events/{ev.id}", json={"user_choice": "accept"})
    row = await only(world.db, "event.choice_set")
    actor_is(row, world.admin)
    assert row.details["owner_user_id"] == str(world.spond.id)


@pytest.mark.asyncio
async def test_touching_someone_elses_event_is_recorded_as_denied(world):
    ev = await _event(world)
    other = await make_login(world.db, "mara")
    async with world.client(other) as c:
        assert (await c.patch(f"/api/v1/events/{ev.id}", json={"user_choice": "accept"})).status_code == 403
    row = await only(world.db, "http.patch")
    assert (row.outcome, row.actor_username, row.path) == ("denied", "mara", f"/api/v1/events/{ev.id}")
    assert await rows(world.db, action="event.choice_set") == []


# ── Admin tools ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_scheduler_actions_are_recorded(world):
    eid = uuid.uuid4()
    with patch("app.workers.executioner.run_sniper", new_callable=AsyncMock), patch("app.workers.discovery.run_discovery", new_callable=AsyncMock):
        async with world.client(world.admin) as c:
            await c.post("/api/v1/admin/sync")
            await c.delete(f"/api/v1/admin/scheduler/sniper_{eid}")
            await c.post(f"/api/v1/admin/scheduler/sniper_{eid}/fire")
    assert [r.action for r in await rows(world.db)] == ["discovery.triggered", "scheduler.job_cancelled", "scheduler.job_fired"]
    cancelled = await only(world.db, "scheduler.job_cancelled")
    assert (cancelled.target_type, cancelled.target_id) == ("event", str(eid))
    actor_is(cancelled, world.admin)


# ── Push ──────────────────────────────────────────────────────────────────


@pytest.fixture
def push_on(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    from generate_vapid_key import generate

    monkeypatch.setattr(settings, "vapid_private_key", generate())


@pytest.mark.asyncio
async def test_turning_notifications_on_and_off_is_recorded_without_the_endpoint(world, push_on):
    endpoint = "https://fcm.googleapis.com/fcm/send/very-secret-device-token"
    keys = {"p256dh": "BNc-key", "auth": "auth-secret"}
    async with world.client(world.felix) as c:
        await c.post("/api/v1/push/subscribe", json={"endpoint": endpoint, "keys": keys})
        with patch("app.services.push.webpush_async", new_callable=AsyncMock):
            await c.post("/api/v1/push/test")
        await c.post("/api/v1/push/unsubscribe", json={"endpoint": endpoint})
    on, test, off = await rows(world.db)
    assert (on.action, on.target_label, on.details) == ("push.subscribed", "fcm.googleapis.com", {"devices": 1})
    assert (test.action, test.outcome, test.details) == ("push.test_sent", "success", {"devices": 1, "delivered": 1})
    assert (off.action, off.target_label) == ("push.unsubscribed", "fcm.googleapis.com")
    dump = str([r.__dict__ for r in await rows(world.db)])
    assert "very-secret-device-token" not in dump and "auth-secret" not in dump


# ── The bot ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bots_answers_are_recorded_as_system_events(world, monkeypatch):
    from app.workers import executioner

    ev = await _event(world, choice="accept", status="processed")
    ev.error_message = None
    await executioner._notify_member(ev, "success")
    ev.status, ev.error_message = "failed", "Spond returned 403"
    await executioner._notify_member(ev, "failed")

    sent, failed = await rows(world.db)
    assert (sent.action, sent.actor_type, sent.outcome, sent.target_label) == ("rsvp.sent", "system", "success", "Training, Hall B")
    assert sent.details == {"choice": "accept", "spond_user_id": str(world.spond.id)}
    assert (failed.action, failed.outcome, failed.details["error"]) == ("rsvp.failed", "failed", "Spond returned 403")
