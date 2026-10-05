# tests/test_audit_service.py — the audit service: scrubbing, actors, the generic safety net,
# what the middleware captures, and that writing the trail can never break a request.
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

import pytest

from app.config import settings
from app.models.audit_log import AuditLog
from app.services import audit
from tests.audit_helpers import client_factory, make_login, only, rows


# ── scrub ─────────────────────────────────────────────────────────────────


def test_secrets_never_survive_scrubbing():
    cleaned = audit.scrub({
        "password": "hunter2", "new_password": "x", "Authorization": "Bearer abc", "api_key": "k",
        "access_token": "t", "spond_password": "p", "cookie": "sb_session=abc", "client_secret": "s",
        "choice": "accept", "nested": {"token": "abc", "ok": 1},
    })
    assert cleaned["choice"] == "accept" and cleaned["nested"] == {"token": "[redacted]", "ok": 1}
    for key in ("password", "new_password", "Authorization", "api_key", "access_token", "spond_password", "cookie", "client_secret"):
        assert cleaned[key] == "[redacted]", key
    assert "hunter2" not in str(cleaned) and "abc" not in str(cleaned).replace("nested", "")


def test_flags_named_like_secrets_are_not_secrets():
    assert audit.scrub({"password_reset": True, "token_present": False, "password": None}) == {
        "password_reset": True, "token_present": False, "password": None}


def test_scrubbing_bounds_the_size_of_what_is_stored():
    cleaned = audit.scrub({"long": "x" * 5000, "many": list(range(500)), "deep": {"a": {"b": {"c": {"d": 1}}}}})
    assert len(cleaned["long"]) == 300 and cleaned["long"].endswith("…")
    assert len(cleaned["many"]) == 20
    assert cleaned["deep"]["a"]["b"] == "…"


def test_scrubbing_makes_everything_json_friendly():
    class Color(Enum):
        RED = 1

    uid, now = uuid.uuid4(), datetime(2026, 10, 5, tzinfo=timezone.utc)
    cleaned = audit.scrub({"id": uid, "at": now, "n": 3, "f": 1.5, "b": True, "none": None, "enum": Color.RED, 5: "int key"})
    assert cleaned["id"] == str(uid) and cleaned["at"] == str(now)
    assert (cleaned["n"], cleaned["f"], cleaned["b"], cleaned["none"]) == (3, 1.5, True, None)
    assert isinstance(cleaned["enum"], str) and cleaned["5"] == "int key"
    import json

    json.dumps(cleaned)


# ── actors ────────────────────────────────────────────────────────────────


def test_actor_from_jwt_claims_and_from_a_login_row():
    uid = uuid.uuid4()
    assert audit.actor_from({"sub": str(uid), "username": "felix", "is_admin": True}) == {"id": uid, "username": "felix", "is_admin": True}

    class Login:
        id, username, is_admin = uid, "mara", False

    assert audit.actor_from(Login()) == {"id": uid, "username": "mara", "is_admin": False}
    assert audit.actor_from(None) is None
    assert audit.actor_from({"sub": "not-a-uuid", "username": "x"})["id"] is None


# ── generic safety net ────────────────────────────────────────────────────


@pytest.mark.parametrize("method,status,path,anonymous,expected", [
    ("POST", 200, "/api/v1/events", False, True),  # any write is recorded
    ("DELETE", 204, "/api/v1/accounts/x", False, True),
    ("PATCH", 422, "/api/v1/events/x", False, True),
    ("PUT", 500, "/api/v1/x", False, True),
    ("GET", 200, "/api/v1/events", False, False),  # successful reads are not
    ("GET", 200, "/dashboard", True, False),
    ("GET", 403, "/api/v1/admin/stats", False, True),  # forbidden is always recorded
    ("GET", 429, "/api/v1/events", True, True),  # rate limited
    ("GET", 401, "/api/v1/auth/me", True, False),  # a signed-out visitor on the sign-in page is normal
    ("POST", 401, "/api/v1/events", True, True),  # an anonymous write attempt is not
    ("GET", 500, "/api/v1/events", False, True),  # server errors
    ("POST", 404, "/wp-login.php", True, False),  # bots probing unknown URLs
    ("POST", 405, "/", True, False),
    ("POST", 404, "/api/v1/events/x", False, True),  # but a signed-in person's miss is recorded
    ("GET", 200, "/api/v1/health", False, False),
    ("GET", 503, "/api/v1/health", False, False),  # Docker polls this every 30 s
])
def test_which_requests_get_a_generic_row(method, status, path, anonymous, expected):
    assert audit.should_log_generic(method, status, path, anonymous) is expected


@pytest.mark.parametrize("status,outcome", [(200, "success"), (204, "success"), (302, "success"), (401, "denied"),
                                            (403, "denied"), (429, "denied"), (404, "failed"), (422, "failed"), (500, "failed")])
def test_outcome_follows_the_status(status, outcome):
    assert audit.outcome_for_status(status) == outcome


def _ctx(method="POST", path="/api/v1/x", token=None):
    return audit.RequestContext(request_id="rid", ip="198.51.100.4", user_agent="UA", method=method, path=path, session_token=token)


def test_a_staged_success_is_downgraded_when_the_request_failed_afterwards(monkeypatch):
    monkeypatch.setattr(settings, "audit_enabled", True)
    for status, outcome in ((500, "failed"), (403, "denied")):
        ctx = _ctx()
        reset = audit._current.set(ctx)
        try:
            audit.record("event.choice_set")
        finally:
            audit._current.reset(reset)
        (row,) = audit._finish_request(ctx, status)
        assert row["outcome"] == outcome and row["status_code"] == status


def test_finished_rows_carry_the_request_context(monkeypatch):
    monkeypatch.setattr(settings, "audit_enabled", True)
    ctx = _ctx(path="/api/v1/events/abc")
    (row,) = audit._finish_request(ctx, 200)
    assert (row["action"], row["category"], row["outcome"]) == ("http.post", "http", "success")
    assert (row["ip"], row["user_agent"], row["method"], row["path"], row["status_code"], row["request_id"]) == (
        "198.51.100.4", "UA", "POST", "/api/v1/events/abc", 200, "rid")
    assert row["actor_type"] == "anonymous"


def test_record_outside_a_request_does_nothing(monkeypatch):
    monkeypatch.setattr(settings, "audit_enabled", True)
    audit.record("event.choice_set")  # must not raise


# ── middleware ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_login_is_recorded_with_where_and_how(audit_on, test_db):
    await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(None, ip="198.51.100.9", user_agent="Mozilla/5.0 Phone") as c:
        resp = await c.post("/api/v1/auth/login", json={"username": "felix", "password": "wrong-password"})
    assert resp.status_code == 401

    row = await only(test_db, "auth.login.failed")
    assert (row.actor_type, row.actor_id, row.outcome) == ("anonymous", None, "denied")
    assert row.target_label == "felix" and row.details == {"reason": "wrong_password"}
    assert (row.ip, row.user_agent) == ("198.51.100.9", "Mozilla/5.0 Phone")
    assert (row.method, row.path, row.status_code) == ("POST", "/api/v1/auth/login", 401)
    assert row.request_id == resp.headers["x-request-id"]
    assert "wrong-password" not in str(row.__dict__)
    assert len(await rows(test_db)) == 1, "the explicit event replaces the generic row"


@pytest.mark.asyncio
async def test_unknown_user_and_wrong_password_are_told_apart_in_the_trail_only(audit_on, test_db):
    client = client_factory(test_db)
    async with client(None) as c:
        a = await c.post("/api/v1/auth/login", json={"username": "ghost", "password": "x" * 10})
    assert a.json() == {"detail": "Incorrect username or password."}
    assert (await only(test_db, "auth.login.failed")).details == {"reason": "unknown_user"}


@pytest.mark.asyncio
async def test_a_very_long_username_is_cut_short(audit_on, test_db):
    client = client_factory(test_db)
    async with client(None) as c:
        await c.post("/api/v1/auth/login", json={"username": "u" * 300, "password": "x" * 10})
    assert len((await only(test_db, "auth.login.failed")).target_label) == 64


@pytest.mark.asyncio
async def test_a_signed_in_write_is_attributed_to_the_login(audit_on, test_db):
    felix = await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(felix) as c:
        resp = await c.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    row = await only(test_db, "auth.logout")
    assert (row.actor_type, row.actor_id, row.actor_username, row.actor_is_admin) == ("user", felix.id, "felix", False)


@pytest.mark.asyncio
async def test_successful_reads_and_health_checks_leave_no_trace(audit_on, test_db):
    felix = await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(felix) as c:
        assert (await c.get("/api/v1/events")).status_code == 200
        await c.get("/api/v1/health")
    async with client(None) as c:
        assert (await c.get("/api/v1/auth/me")).status_code == 401  # signed-out visitor on the sign-in page
        assert (await c.get("/")).status_code == 200
    assert await rows(test_db) == []


@pytest.mark.asyncio
async def test_a_forbidden_request_is_recorded_as_denied(audit_on, test_db):
    felix = await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(felix) as c:
        assert (await c.get("/api/v1/admin/stats")).status_code == 403
    row = await only(test_db, "http.get")
    assert (row.outcome, row.status_code, row.path, row.actor_username) == ("denied", 403, "/api/v1/admin/stats", "felix")


@pytest.mark.asyncio
async def test_an_anonymous_write_attempt_is_recorded(audit_on, test_db):
    client = client_factory(test_db)
    async with client(None) as c:
        assert (await c.patch(f"/api/v1/events/{uuid.uuid4()}", json={"user_choice": "accept"})).status_code == 401
    row = await only(test_db, "http.patch")
    assert (row.actor_type, row.outcome) == ("anonymous", "denied")


@pytest.mark.asyncio
async def test_a_forged_session_cookie_is_anonymous(audit_on, test_db):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"sb_session": "forged.jwt.value"}) as c:
        await c.patch(f"/api/v1/events/{uuid.uuid4()}", json={"user_choice": "accept"})
    row = await only(test_db, "http.patch")
    assert (row.actor_type, row.actor_id, row.actor_username) == ("anonymous", None, None)


@pytest.mark.asyncio
async def test_brute_forcing_the_login_shows_up_as_denied_and_rate_limited(audit_on, test_db):
    client = client_factory(test_db)
    async with client(None, ip="198.51.100.66") as c:
        codes = [(await c.post("/api/v1/auth/login", json={"username": "admin", "password": "guess-%d" % i})).status_code
                 for i in range(7)]
    assert codes == [401] * 5 + [429] * 2
    failed = await rows(test_db, action="auth.login.failed")
    limited = await rows(test_db, action="http.post")
    assert len(failed) == 5 and len(limited) == 2
    assert {r.outcome for r in limited} == {"denied"} and {r.status_code for r in limited} == {429}
    assert {r.ip for r in failed + limited} == {"198.51.100.66"}


@pytest.mark.asyncio
async def test_a_signed_in_persons_missing_target_is_recorded_but_a_bots_is_not(audit_on, test_db):
    felix = await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(None) as c:
        await c.post("/wp-login.php")
    assert await rows(test_db) == []
    async with client(felix) as c:
        assert (await c.patch(f"/api/v1/events/{uuid.uuid4()}", json={"user_choice": "accept"})).status_code == 404
    assert (await only(test_db, "http.patch")).outcome == "failed"


@pytest.mark.asyncio
async def test_writing_the_trail_can_never_break_a_request(audit_on, test_db, monkeypatch):
    class Broken:
        async def __aenter__(self):
            raise ConnectionError("database down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(audit, "open_session", lambda: Broken())
    felix = await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(felix) as c:
        resp = await c.post("/api/v1/auth/logout")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_the_audit_trail_is_off(audit_on, test_db, monkeypatch):
    monkeypatch.setattr(settings, "audit_enabled", False)
    felix = await make_login(test_db, "felix")
    client = client_factory(test_db)
    async with client(felix) as c:
        resp = await c.post("/api/v1/auth/logout")
    assert resp.status_code == 204 and "x-request-id" not in resp.headers
    assert await rows(test_db) == []


@pytest.mark.asyncio
async def test_every_response_names_its_request(audit_on, test_db):
    client = client_factory(test_db)
    async with client(None) as c:
        a = await c.get("/api/v1/health")
        b = await c.get("/api/v1/health")
    assert a.headers["x-request-id"] != b.headers["x-request-id"]
    uuid.UUID(a.headers["x-request-id"])


@pytest.mark.asyncio
async def test_the_ip_is_what_the_server_saw_not_what_the_client_claims(audit_on, test_db):
    """X-Forwarded-For is only honoured by uvicorn for trusted proxies; the app never reads it itself."""
    client = client_factory(test_db)
    async with client(None, ip="198.51.100.20") as c:
        await c.post("/api/v1/auth/login", json={"username": "a", "password": "b" * 10},
                     headers={"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"})
    assert (await only(test_db, "auth.login.failed")).ip == "198.51.100.20"


# ── system events ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bots_own_actions_are_recorded_as_system(audit_on, test_db):
    await audit.record_system("rsvp.sent", target_type="event", target_id=uuid.uuid4(), target_label="Training",
                              details={"choice": "accept", "password": "never"})
    row = await only(test_db, "rsvp.sent")
    assert (row.actor_type, row.actor_id, row.actor_username, row.ip, row.method) == ("system", None, None, None, None)
    assert row.category == "rsvp" and row.details == {"choice": "accept", "password": "[redacted]"}


@pytest.mark.asyncio
async def test_record_system_is_silent_when_off_and_never_raises(audit_on, test_db, monkeypatch):
    class Broken:
        async def __aenter__(self):
            raise ConnectionError("down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(audit, "open_session", lambda: Broken())
    await audit.record_system("rsvp.sent")
    monkeypatch.setattr(settings, "audit_enabled", False)
    await audit.record_system("rsvp.sent")


# ── retention ─────────────────────────────────────────────────────────────


def _row(days_ago: int, action="x.y") -> AuditLog:
    return AuditLog(occurred_at=datetime.now(timezone.utc) - timedelta(days=days_ago), actor_type="system",
                    action=action, category="x", outcome="success")


@pytest.mark.asyncio
async def test_purge_removes_only_entries_past_the_retention(test_db):
    test_db.add_all([_row(200, "old.a"), _row(91, "old.b"), _row(89, "new.a"), _row(0, "new.b")])
    await test_db.commit()
    assert await audit.purge_old(test_db, 90) == 2
    assert {r.action for r in await rows(test_db)} == {"new.a", "new.b"}
    assert await audit.purge_old(test_db, 90) == 0


@pytest.mark.asyncio
async def test_the_nightly_purge_uses_the_configured_retention_and_leaves_a_note(audit_on, test_db, monkeypatch):
    monkeypatch.setattr(settings, "audit_retention_days", 30)
    test_db.add_all([_row(45, "old"), _row(5, "recent")])
    await test_db.commit()
    await audit.run_purge()
    actions = [r.action for r in await rows(test_db)]
    assert "old" not in actions and "recent" in actions
    note = await only(test_db, "audit.purged")
    assert note.actor_type == "system" and note.details == {"deleted": 1, "older_than_days": 30}


@pytest.mark.asyncio
async def test_the_nightly_purge_is_quiet_when_there_is_nothing_to_remove(audit_on, test_db):
    test_db.add(_row(1))
    await test_db.commit()
    await audit.run_purge()
    assert await rows(test_db, action="audit.purged") == []


@pytest.mark.asyncio
async def test_the_nightly_purge_survives_a_broken_database(audit_on, monkeypatch):
    class Broken:
        async def __aenter__(self):
            raise ConnectionError("down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(audit, "open_session", lambda: Broken())
    await audit.run_purge()


@pytest.mark.asyncio
async def test_the_purge_job_is_scheduled_nightly():
    from app.workers import scheduler

    scheduler.start_scheduler()
    try:
        job = scheduler.get_scheduler().get_job("audit_purge")
        assert job is not None
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "3" and fields["minute"] == "17"
    finally:
        scheduler.shutdown_scheduler()
