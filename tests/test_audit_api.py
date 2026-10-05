# tests/test_audit_api.py — reading the audit trail: admin only, filters, pagination, CSV export.
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditLog
from tests.audit_helpers import client_factory, make_login, only, rows

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def entry(minutes_ago, action="event.choice_set", *, actor="felix", outcome="success", target=None, ip="203.0.113.7",
          actor_id=None, details=None, path=None) -> AuditLog:
    return AuditLog(
        occurred_at=NOW - timedelta(minutes=minutes_ago), actor_type="user" if actor else "anonymous",
        actor_id=actor_id, actor_username=actor, action=action, category=action.split(".")[0], outcome=outcome,
        target_label=target, ip=ip, details=details, path=path, method="POST", status_code=200 if outcome == "success" else 403,
    )


@pytest.fixture
async def admin_and_data(audit_on, test_db):
    admin = await make_login(test_db, "admin", admin=True)
    member = await make_login(test_db, "felix")
    test_db.add_all([
        entry(1, "auth.login.success", actor="felix", ip="198.51.100.1"),
        entry(2, "event.choice_set", actor="felix", target="Training, Hall B", details={"from": "manual", "to": "accept"}),
        entry(3, "auth.login.failed", actor=None, outcome="denied", target="admin", ip="192.0.2.99"),
        entry(4, "account.created", actor="admin", target="mara"),
        entry(60, "event.choice_set", actor="mara", target="Evening training"),
        entry(60 * 30, "invite.created", actor="admin", target="Jonas"),
    ])
    await test_db.commit()
    return type("Ctx", (), {"admin": admin, "member": member, "client": client_factory(test_db), "db": test_db})


async def fetch(ctx, **params):
    async with ctx.client(ctx.admin) as c:
        resp = await c.get("/api/v1/admin/audit", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_only_admins_can_read_the_trail(admin_and_data):
    ctx = admin_and_data
    async with ctx.client(ctx.member) as c:
        assert (await c.get("/api/v1/admin/audit")).status_code == 403
        assert (await c.get("/api/v1/admin/audit/export.csv")).status_code == 403
    async with ctx.client(None) as c:
        assert (await c.get("/api/v1/admin/audit")).status_code == 401
        assert (await c.get("/api/v1/admin/audit/export.csv")).status_code == 401


@pytest.mark.asyncio
async def test_newest_first_with_everything_an_admin_needs(admin_and_data):
    page = await fetch(admin_and_data)
    actions = [i["action"] for i in page["items"]]
    assert actions == ["auth.login.success", "event.choice_set", "auth.login.failed", "account.created", "event.choice_set", "invite.created"]
    assert page["next_cursor"] is None
    choice = page["items"][1]
    assert choice["details"] == {"from": "manual", "to": "accept"} and choice["actor_username"] == "felix"
    assert set(choice) >= {"id", "occurred_at", "actor_type", "actor_id", "actor_is_admin", "category", "outcome", "target_type",
                           "target_id", "target_label", "ip", "user_agent", "method", "path", "status_code", "request_id"}


@pytest.mark.asyncio
async def test_reading_the_trail_leaves_no_entry_of_its_own(admin_and_data):
    before = len(await rows(admin_and_data.db))
    await fetch(admin_and_data)
    await fetch(admin_and_data, q="felix")
    assert len(await rows(admin_and_data.db)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("params,expected", [
    ({"category": "auth"}, ["auth.login.success", "auth.login.failed"]),
    ({"outcome": "denied"}, ["auth.login.failed"]),
    ({"outcome": "success", "category": "event"}, ["event.choice_set", "event.choice_set"]),
    ({"q": "mara"}, ["account.created", "event.choice_set"]),       # target "mara" and actor "mara"
    ({"q": "HALL b"}, ["event.choice_set"]),                         # case-insensitive
    ({"q": "192.0.2.99"}, ["auth.login.failed"]),                    # by IP
    ({"q": "login"}, ["auth.login.success", "auth.login.failed"]),   # by action text
    ({"q": "nothing-matches-this"}, []),
])
async def test_filters_narrow_the_trail(admin_and_data, params, expected):
    page = await fetch(admin_and_data, **params)
    assert [i["action"] for i in page["items"]] == expected


@pytest.mark.asyncio
async def test_search_text_is_not_a_pattern(admin_and_data):
    assert (await fetch(admin_and_data, q="%"))["items"] == []
    assert (await fetch(admin_and_data, q="c_oice"))["items"] == []  # "_" is a letter here, not "any character"
    assert len((await fetch(admin_and_data, q="choice_set"))["items"]) == 2


@pytest.mark.asyncio
async def test_time_range_and_actor_filters(admin_and_data):
    ctx = admin_and_data
    since = (NOW - timedelta(minutes=10)).isoformat()
    assert len((await fetch(ctx, since=since))["items"]) == 4
    assert len((await fetch(ctx, until=(NOW - timedelta(minutes=10)).isoformat()))["items"]) == 2
    actor = uuid.uuid4()
    ctx.db.add(entry(0, "auth.logout", actor="zed", actor_id=actor))
    await ctx.db.commit()
    page = await fetch(ctx, actor_id=str(actor))
    assert [i["action"] for i in page["items"]] == ["auth.logout"]


@pytest.mark.asyncio
async def test_pages_follow_a_cursor_without_gaps_or_repeats(admin_and_data):
    ctx = admin_and_data
    seen, cursor = [], None
    for _ in range(10):
        page = await fetch(ctx, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    everything = [i["id"] for i in (await fetch(ctx))["items"]]
    assert seen == everything and len(set(seen)) == 6


@pytest.mark.asyncio
async def test_entries_with_the_same_timestamp_are_not_skipped_between_pages(audit_on, test_db):
    admin = await make_login(test_db, "admin", admin=True)
    test_db.add_all([entry(5, f"x.same{i}") for i in range(5)])
    await test_db.commit()
    ctx = type("Ctx", (), {"admin": admin, "client": client_factory(test_db)})
    seen, cursor = [], None
    while True:
        page = await fetch(ctx, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += [i["action"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == [f"x.same{i}" for i in range(5)]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"outcome": "weird"}, {"cursor": "garbage"},
                                    {"cursor": "2026-10-05T00:00:00|not-a-uuid"}, {"actor_id": "nope"}, {"since": "yesterday"}])
async def test_bad_parameters_are_rejected(admin_and_data, params):
    ctx = admin_and_data
    async with ctx.client(ctx.admin) as c:
        assert (await c.get("/api/v1/admin/audit", params=params)).status_code == 422


# ── CSV ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_csv_export_honours_the_filters_and_records_the_export(admin_and_data):
    ctx = admin_and_data
    async with ctx.client(ctx.admin) as c:
        resp = await c.get("/api/v1/admin/audit/export.csv", params={"category": "auth"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="spondbot-audit.csv"' == resp.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(resp.text)))
    assert [r["action"] for r in table] == ["auth.login.success", "auth.login.failed"]
    assert table[1]["ip"] == "192.0.2.99" and table[1]["outcome"] == "denied" and table[1]["target_label"] == "admin"

    export = await only(ctx.db, "audit.exported")
    assert (export.actor_username, export.details["rows"], export.details["category"]) == ("admin", 2, "auth")


@pytest.mark.asyncio
async def test_csv_cells_cannot_run_formulas_in_a_spreadsheet(audit_on, test_db):
    admin = await make_login(test_db, "admin", admin=True)
    test_db.add(entry(1, "auth.login.failed", actor=None, outcome="denied", target='=HYPERLINK("http://evil","click")',
                      details={"note": "+cmd|' /C calc'!A0"}, path="@SUM(A1)"))
    await test_db.commit()
    async with client_factory(test_db)(admin) as c:
        text = (await c.get("/api/v1/admin/audit/export.csv")).text
    row = next(r for r in csv.DictReader(io.StringIO(text)) if r["action"] == "auth.login.failed")
    assert row["target_label"].startswith("'=") and row["path"] == "'@SUM(A1)"
    assert not any(v[:1] in "=+@" and v for r in csv.DictReader(io.StringIO(text)) for v in r.values() if v and v[:1] != "{")


@pytest.mark.asyncio
async def test_csv_has_a_stable_header_even_when_empty(audit_on, test_db):
    admin = await make_login(test_db, "admin", admin=True)
    async with client_factory(test_db)(admin) as c:
        text = (await c.get("/api/v1/admin/audit/export.csv", params={"q": "zzz"})).text
    header = text.splitlines()[0]
    assert header.startswith("occurred_at,actor_type,actor_username,action,outcome") and header.endswith("request_id,details")
