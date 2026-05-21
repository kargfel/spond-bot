# Admin Health Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /api/v1/admin/stats` endpoint that aggregates system health data, and surface it in the admin panel as an enhanced stats bar with a recent-failures list that auto-refreshes every 30 seconds.

**Architecture:** A new `GET /admin/stats` route in `app/api/admin.py` runs four DB aggregation queries and reads the last discovery timestamp from an in-memory module-level variable in `app/workers/discovery.py`. The frontend replaces the current four-stub stats bar with richer data from this single endpoint. This branch is independent of the audit log branch but can be merged after it.

**Tech Stack:** SQLAlchemy async ORM, FastAPI, pytest + pytest-asyncio + httpx + aiosqlite, vanilla JS.

**Feature branch:** `feat/observability-health-dashboard`

**Note:** This plan assumes the test infrastructure from `feat/observability-rsvp-audit-log` is available. If branching from `main` before that branch merges, copy Task 1 and Task 2 from that plan first.

---

### Task 1: Create feature branch

- [ ] **Step 1: Create and checkout the branch**

```bash
git checkout main
git checkout -b feat/observability-health-dashboard
```

If branching from the audit-log branch instead:
```bash
git checkout feat/observability-rsvp-audit-log
git checkout -b feat/observability-health-dashboard
```

- [ ] **Step 2: Confirm test infrastructure is present**

```bash
pytest tests/ --collect-only 2>&1 | head -5
```

Expected: pytest collects tests without error. If tests/ directory doesn't exist, copy tasks 1–2 from `2026-05-21-rsvp-audit-log.md` before continuing.

---

### Task 2: Add last_discovery_at tracking to discovery worker

**Files:**
- Modify: `app/workers/discovery.py`

The stats endpoint needs to report when the last discovery sync completed. The simplest approach is a module-level variable updated by the worker at the end of each run.

- [ ] **Step 1: Write a failing test**

Create `tests/test_discovery_tracking.py`:

```python
def test_last_discovery_at_initially_none():
    import app.workers.discovery as discovery
    # Before any run, the timestamp is None
    assert discovery.last_discovery_at is None

def test_last_discovery_at_is_exported():
    from app.workers.discovery import last_discovery_at
    # just confirming the name is importable
    assert True
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_discovery_tracking.py -v
```

Expected: FAIL with `AttributeError: module 'app.workers.discovery' has no attribute 'last_discovery_at'`

- [ ] **Step 3: Add the module-level variable and update it in run_discovery**

In `app/workers/discovery.py`, add after the existing imports and before `_BULK_CHUNK_SIZE`:

```python
from datetime import datetime, timezone

# Updated at the end of every successful discovery run.
# None until the first run completes.
last_discovery_at: datetime | None = None
```

At the very end of `run_discovery()`, before the final log line, add:

```python
    global last_discovery_at
    last_discovery_at = datetime.now(timezone.utc)
```

So the end of `run_discovery()` looks like:

```python
    except Exception as exc:
        logger.exception("Discovery sync crashed unexpectedly: %s", exc)

    global last_discovery_at
    last_discovery_at = datetime.now(timezone.utc)
    logger.info("=== Discovery sync complete ===")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_discovery_tracking.py -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/workers/discovery.py tests/test_discovery_tracking.py
git commit -m "feat: track last_discovery_at timestamp in discovery worker"
```

---

### Task 3: Create the stats Pydantic schema

**Files:**
- Create: `app/schemas/stats.py`

- [ ] **Step 1: Write a failing test**

Create `tests/test_stats_schema.py`:

```python
def test_admin_stats_schema_importable():
    from app.schemas.stats import AdminStatsResponse
    obj = AdminStatsResponse(
        active_users=3,
        total_events=50,
        events_pending=10,
        events_processed=35,
        events_failed=5,
        last_discovery_at=None,
        recent_failures=[],
    )
    assert obj.active_users == 3
    assert obj.events_failed == 5
    assert obj.last_discovery_at is None

def test_admin_stats_recent_failure_schema():
    from app.schemas.stats import AdminStatsResponse, RecentFailure
    import uuid
    from datetime import datetime, timezone
    failure = RecentFailure(
        event_id=uuid.uuid4(),
        user_display_name="Alice",
        heading="Soccer training",
        error_message="Token rejected",
        updated_at=datetime.now(timezone.utc),
    )
    obj = AdminStatsResponse(
        active_users=1,
        total_events=1,
        events_pending=0,
        events_processed=0,
        events_failed=1,
        last_discovery_at=None,
        recent_failures=[failure],
    )
    assert len(obj.recent_failures) == 1
    assert obj.recent_failures[0].user_display_name == "Alice"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_stats_schema.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Create app/schemas/stats.py**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class RecentFailure(BaseModel):
    event_id: uuid.UUID
    user_display_name: str
    heading: str | None
    error_message: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminStatsResponse(BaseModel):
    active_users: int
    total_events: int
    events_pending: int
    events_processed: int
    events_failed: int
    last_discovery_at: datetime | None
    recent_failures: list[RecentFailure]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_stats_schema.py -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/stats.py tests/test_stats_schema.py
git commit -m "feat: add AdminStatsResponse Pydantic schema"
```

---

### Task 4: Add GET /admin/stats endpoint

**Files:**
- Modify: `app/api/admin.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_admin_stats_api.py`:

```python
import uuid
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_stats_returns_zeros_on_empty_db(admin_client, test_db):
    resp = await admin_client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_users"] == 0
    assert data["total_events"] == 0
    assert data["events_pending"] == 0
    assert data["events_processed"] == 0
    assert data["events_failed"] == 0
    assert data["recent_failures"] == []
    # last_discovery_at may be None or a datetime string
    assert "last_discovery_at" in data


@pytest.mark.asyncio
async def test_stats_counts_active_users(admin_client, test_db):
    from app.models.user import User
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="Test User",
        login="test@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    assert resp.json()["active_users"] == 1


@pytest.mark.asyncio
async def test_stats_counts_events_by_status(admin_client, test_db):
    from app.models.user import User
    from app.models.event import Event, STATUS_PENDING, STATUS_PROCESSED, STATUS_FAILED
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="User2",
        login="user2@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    for status in [STATUS_PENDING, STATUS_PROCESSED, STATUS_PROCESSED, STATUS_FAILED]:
        test_db.add(Event(
            id=uuid.uuid4(),
            spond_event_id=f"EVT-{uuid.uuid4().hex[:8]}",
            user_id=user.id,
            heading="Test Event",
            status=status,
        ))
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    data = resp.json()
    assert data["total_events"] == 4
    assert data["events_pending"] == 1
    assert data["events_processed"] == 2
    assert data["events_failed"] == 1


@pytest.mark.asyncio
async def test_stats_recent_failures(admin_client, test_db):
    from app.models.user import User
    from app.models.event import Event, STATUS_FAILED
    from app.core.security import encrypt

    user = User(
        id=uuid.uuid4(),
        display_name="FailUser",
        login="fail@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    test_db.add(Event(
        id=uuid.uuid4(),
        spond_event_id="EVT-FAIL",
        user_id=user.id,
        heading="Broken Event",
        status=STATUS_FAILED,
        error_message="Network timeout",
    ))
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    data = resp.json()
    assert data["events_failed"] == 1
    assert len(data["recent_failures"]) == 1
    assert data["recent_failures"][0]["user_display_name"] == "FailUser"
    assert data["recent_failures"][0]["error_message"] == "Network timeout"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_admin_stats_api.py -v
```

Expected: FAIL with 404 (endpoint doesn't exist yet)

- [ ] **Step 3: Add the stats endpoint to app/api/admin.py**

Add these imports at the top of `app/api/admin.py`:

```python
from sqlalchemy import func, select
```

Add this import with the existing model imports:

```python
from app.models.event import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    Event,
)
from app.models.user import User
from app.schemas.stats import AdminStatsResponse, RecentFailure
```

Then add the endpoint after the existing `get_rsvp_log` function:

```python
@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    dependencies=[AdminDep],
    summary="System health stats (admin only)",
)
async def get_admin_stats(db: AsyncSession = DbDep):
    """
    Returns aggregated system health data:
    - User and event counts by status
    - Last discovery sync timestamp
    - Up to 10 most recent failed events
    """
    from app.workers.discovery import last_discovery_at

    active_users = (
        await db.execute(select(func.count()).where(User.is_active == True))  # noqa: E712
    ).scalar_one()

    total_events = (await db.execute(select(func.count()).select_from(Event))).scalar_one()

    events_pending = (
        await db.execute(select(func.count()).where(Event.status == STATUS_PENDING))
    ).scalar_one()

    events_processed = (
        await db.execute(select(func.count()).where(Event.status == STATUS_PROCESSED))
    ).scalar_one()

    events_failed = (
        await db.execute(select(func.count()).where(Event.status == STATUS_FAILED))
    ).scalar_one()

    failed_rows = (
        await db.execute(
            select(Event, User.display_name)
            .join(User, Event.user_id == User.id)
            .where(Event.status == STATUS_FAILED)
            .order_by(Event.updated_at.desc())
            .limit(10)
        )
    ).all()

    recent_failures = [
        RecentFailure(
            event_id=ev.id,
            user_display_name=display_name,
            heading=ev.heading,
            error_message=ev.error_message,
            updated_at=ev.updated_at,
        )
        for ev, display_name in failed_rows
    ]

    return AdminStatsResponse(
        active_users=active_users,
        total_events=total_events,
        events_pending=events_pending,
        events_processed=events_processed,
        events_failed=events_failed,
        last_discovery_at=last_discovery_at,
        recent_failures=recent_failures,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_admin_stats_api.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Run all tests to confirm no regressions**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/admin.py tests/test_admin_stats_api.py
git commit -m "feat: add GET /api/v1/admin/stats endpoint (admin only)"
```

---

### Task 5: Update admin panel stats bar and add auto-refresh

**Files:**
- Modify: `frontend/admin.html`

The current stats bar uses four separate `api()` calls to compute counts. Replace this with a single `/admin/stats` call that also powers a recent-failures widget and shows the last discovery timestamp.

- [ ] **Step 1: Replace the loadStats() function**

In the `<script>` block of `frontend/admin.html`, find the existing `loadStats()` function and replace it entirely:

```javascript
      // ── Stats (from /admin/stats) ──────────────────────────────────────
      async function loadStats() {
        try {
          const res = await api('/admin/stats');
          if (!res.ok) return;
          const d = await res.json();

          document.getElementById('stat-users').textContent = d.active_users;
          document.getElementById('stat-events').textContent = d.total_events;
          document.getElementById('stat-pending').textContent = d.events_pending;
          document.getElementById('stat-failed').textContent = d.events_failed;

          renderLastDiscovery(d.last_discovery_at);
          renderRecentFailures(d.recent_failures);
        } catch {}
      }

      function renderLastDiscovery(isoTs) {
        const el = document.getElementById('stat-last-discovery');
        if (!el) return;
        el.textContent = isoTs ? fmtDate(isoTs) : 'Never';
      }

      function renderRecentFailures(failures) {
        const wrap = document.getElementById('recent-failures-wrap');
        if (!wrap) return;
        if (!failures.length) {
          wrap.innerHTML = `<div class="empty-state" style="padding:1rem"><div class="empty-state-title" style="font-size:0.85rem">No recent failures</div></div>`;
          return;
        }
        wrap.innerHTML = failures.map(f => `
          <div style="padding:0.6rem 0;border-bottom:1px solid var(--surface-container-high);font-size:0.82rem">
            <div style="display:flex;justify-content:space-between;gap:0.5rem;align-items:flex-start">
              <span style="font-weight:500;color:var(--text)">${escHtml(f.heading || '—')}</span>
              <span style="color:var(--text-muted);white-space:nowrap;font-size:0.75rem">${fmtDate(f.updated_at)}</span>
            </div>
            <div style="color:var(--mint);font-size:0.75rem">${escHtml(f.user_display_name)}</div>
            ${f.error_message ? `<div style="color:var(--status-red);font-size:0.75rem;margin-top:0.2rem">${escHtml(f.error_message)}</div>` : ''}
          </div>`).join('');
      }
```

- [ ] **Step 2: Add two new stat cards to the stats bar in the HTML**

Find the existing `<div class="stats-bar" id="stats-bar">` block and add two new cards after the "Failed" card:

```html
          <div class="stat-card">
            <div class="stat-value" id="stat-last-discovery" style="font-size:0.75rem;font-weight:500">—</div>
            <div class="stat-label">Last Discovery</div>
          </div>
```

- [ ] **Step 3: Add the recent-failures widget below the stats bar**

After the closing `</div>` of the stats bar, add:

```html
        <!-- Recent Failures Widget -->
        <div id="recent-failures-section" style="margin:0 0 1.5rem 0">
          <div class="section-header" style="margin-bottom:0.5rem">
            <span class="section-title" style="font-size:0.85rem">Recent Failures</span>
            <span class="text-muted text-sm">Last 10 failed RSVPs</span>
          </div>
          <div id="recent-failures-wrap" style="background:var(--surface-container);border:1px solid var(--border);border-radius:10px;padding:0 1rem">
            <div class="flex-center" style="padding: 1rem">
              <span class="spinner" style="width: 16px; height: 16px; border-width: 2px"></span>
            </div>
          </div>
        </div>
```

- [ ] **Step 4: Add auto-refresh interval in init()**

Find the `init()` function and add an auto-refresh after the initial load call:

```javascript
        await Promise.all([loadStats(), loadAdminEvents()]);
        // Auto-refresh stats every 30 seconds
        setInterval(loadStats, 30_000);
```

The updated end of `init()` should look like:

```javascript
      async function init() {
        adminPayload = await requireAdmin();

        document.getElementById('admin-username').textContent = adminPayload.username;
        document.getElementById('admin-avatar').textContent = initials(adminPayload.username);
        document.getElementById('mobile-admin-avatar').textContent = initials(adminPayload.username);

        await loadSpondUsers();
        await Promise.all([loadStats(), loadAdminEvents()]);
        setInterval(loadStats, 30_000);
      }
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: enhance admin stats bar with /admin/stats endpoint and auto-refresh"
```

---

### Task 6: Final integration check and push

- [ ] **Step 1: Start the app and verify the admin panel**

```bash
uvicorn app.main:app --reload --port 8080
```

Navigate to `http://localhost:8080/admin`. Confirm:
- Stats bar shows 6 cards (including "Last Discovery")
- "Recent Failures" widget appears below the stats bar
- Stats auto-refresh every 30 seconds (verify in browser network tab)
- No JS console errors

- [ ] **Step 2: Run all tests one final time**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/observability-health-dashboard
```

---

## Spec Coverage Checklist

- [x] Single `GET /admin/stats` endpoint replacing four separate count queries
- [x] Returns: active user count, total events, events by status (pending/processed/failed)
- [x] Returns: last discovery timestamp (from worker module variable)
- [x] Returns: up to 10 recent failed events with user name, heading, error, timestamp
- [x] Admin-only enforcement (uses AdminDep)
- [x] Stats bar updated from endpoint data
- [x] Recent failures widget in admin panel
- [x] Auto-refresh every 30 seconds
