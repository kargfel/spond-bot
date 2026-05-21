# Timing Precision Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute the delta between `invite_time` and `submitted_at` for each RSVP, expose p50/p95 latency in `GET /admin/stats`, and surface it in the admin panel. Optionally add a configurable `RSVP_LEAD_TIME_MS` offset so the sniper fires slightly early to compensate for network round-trip time.

**Architecture:** The `rsvp_log.submitted_at` column (added in the audit-log branch) already captures when the API call was dispatched. This branch adds `delta_ms` computation in the stats endpoint using a SQL percentile query over `(submitted_at - events.invite_time)`, and adds a `rsvp_lead_time_ms` config var that offsets sniper fire time.

**Tech Stack:** SQLAlchemy async ORM, PostgreSQL `EXTRACT(EPOCH FROM ...)` for delta computation, FastAPI, pytest, vanilla JS.

**Feature branch:** `feat/observability-timing-metrics`

**Prerequisite:** `feat/observability-rsvp-audit-log` must be merged (or this branch must start from it), because this plan requires the `rsvp_log` table and `submitted_at` column.

---

### Task 1: Create feature branch

- [ ] **Step 1: Create branch from the audit-log branch (or main after it merges)**

If audit-log is merged to main:
```bash
git checkout main && git pull
git checkout -b feat/observability-timing-metrics
```

If branching before merge:
```bash
git checkout feat/observability-rsvp-audit-log
git checkout -b feat/observability-timing-metrics
```

- [ ] **Step 2: Confirm rsvp_log table and submitted_at exist**

```bash
python -c "from app.models.rsvp_log import RsvpLog; print(RsvpLog.submitted_at)"
```

Expected: prints the column descriptor without error.

---

### Task 2: Add timing stats to the schema

**Files:**
- Modify: `app/schemas/stats.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_stats_schema.py`:

```python
def test_admin_stats_has_timing_fields():
    from app.schemas.stats import AdminStatsResponse
    import uuid
    obj = AdminStatsResponse(
        active_users=1,
        total_events=1,
        events_pending=0,
        events_processed=1,
        events_failed=0,
        last_discovery_at=None,
        recent_failures=[],
        rsvp_p50_ms=120,
        rsvp_p95_ms=450,
        rsvp_sample_count=10,
    )
    assert obj.rsvp_p50_ms == 120
    assert obj.rsvp_p95_ms == 450
    assert obj.rsvp_sample_count == 10
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_stats_schema.py::test_admin_stats_has_timing_fields -v
```

Expected: FAIL with `ValidationError` — unknown fields

- [ ] **Step 3: Add timing fields to app/schemas/stats.py**

Replace the `AdminStatsResponse` class in `app/schemas/stats.py`:

```python
class AdminStatsResponse(BaseModel):
    active_users: int
    total_events: int
    events_pending: int
    events_processed: int
    events_failed: int
    last_discovery_at: datetime | None
    recent_failures: list[RecentFailure]
    # Timing metrics over last 200 RSVP submissions (None if no data)
    rsvp_p50_ms: int | None
    rsvp_p95_ms: int | None
    rsvp_sample_count: int
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_stats_schema.py -v
```

Expected: All tests in that file PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/stats.py tests/test_stats_schema.py
git commit -m "feat: add rsvp timing fields to AdminStatsResponse schema"
```

---

### Task 3: Compute timing percentiles in the stats endpoint

**Files:**
- Modify: `app/api/admin.py`

The computation uses the `rsvp_log` table's `submitted_at` column and the `events` table's `invite_time`. Delta = `submitted_at - invite_time` in milliseconds. We compute p50/p95 over the last 200 rows that have both values.

- [ ] **Step 1: Write a failing test**

Add to `tests/test_admin_stats_api.py`:

```python
@pytest.mark.asyncio
async def test_stats_timing_metrics_present(admin_client, test_db):
    resp = await admin_client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    data = resp.json()
    # Fields must be present (None when no data)
    assert "rsvp_p50_ms" in data
    assert "rsvp_p95_ms" in data
    assert "rsvp_sample_count" in data
    assert data["rsvp_sample_count"] == 0
    assert data["rsvp_p50_ms"] is None
    assert data["rsvp_p95_ms"] is None


@pytest.mark.asyncio
async def test_stats_timing_metrics_with_data(admin_client, test_db):
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS
    from app.models.user import User
    from app.models.event import Event, STATUS_PROCESSED
    from app.core.security import encrypt
    from datetime import timedelta
    import uuid

    user = User(
        id=uuid.uuid4(),
        display_name="TimingUser",
        login="timing@example.com",
        encrypted_password=encrypt("fakepass"),
        is_active=True,
    )
    test_db.add(user)
    await test_db.flush()

    invite_time = datetime.now(timezone.utc) - timedelta(minutes=5)

    for delay_ms in [100, 200, 300, 400, 500]:
        ev = Event(
            id=uuid.uuid4(),
            spond_event_id=f"EVT-T-{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            heading="Timed Event",
            status=STATUS_PROCESSED,
            invite_time=invite_time,
        )
        test_db.add(ev)
        await test_db.flush()

        submitted = invite_time + timedelta(milliseconds=delay_ms)
        test_db.add(RsvpLog(
            id=uuid.uuid4(),
            event_id=ev.id,
            user_id=user.id,
            spond_event_id=ev.spond_event_id,
            choice="accept",
            fired_at=invite_time,
            submitted_at=submitted,
            outcome=OUTCOME_SUCCESS,
            retry_count=0,
        ))

    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/stats")
    data = resp.json()
    assert data["rsvp_sample_count"] == 5
    # p50 of [100,200,300,400,500] should be around 300ms
    assert data["rsvp_p50_ms"] is not None
    assert 200 <= data["rsvp_p50_ms"] <= 400
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_admin_stats_api.py::test_stats_timing_metrics_present tests/test_admin_stats_api.py::test_stats_timing_metrics_with_data -v
```

Expected: FAIL — `rsvp_p50_ms` field missing from response (schema mismatch)

- [ ] **Step 3: Add timing computation to get_admin_stats in app/api/admin.py**

Add these imports at the top of `app/api/admin.py` (with existing imports):

```python
from sqlalchemy import text
from app.models.rsvp_log import RsvpLog
```

In the `get_admin_stats` function, add the timing computation before the `return` statement:

```python
    # Compute timing percentiles from the last 200 successful RSVP submissions
    # that have both submitted_at and the event's invite_time.
    # Delta = submitted_at - invite_time in milliseconds.
    # Uses Python-side percentile since SQLite (tests) doesn't support percentile_cont.
    from sqlalchemy import and_

    timing_rows = (
        await db.execute(
            select(
                RsvpLog.submitted_at,
                Event.invite_time,
            )
            .join(Event, RsvpLog.event_id == Event.id)
            .where(
                and_(
                    RsvpLog.submitted_at.is_not(None),
                    Event.invite_time.is_not(None),
                )
            )
            .order_by(RsvpLog.fired_at.desc())
            .limit(200)
        )
    ).all()

    rsvp_p50_ms = None
    rsvp_p95_ms = None
    rsvp_sample_count = len(timing_rows)

    if timing_rows:
        deltas_ms = sorted(
            int((row.submitted_at - row.invite_time).total_seconds() * 1000)
            for row in timing_rows
            if row.submitted_at and row.invite_time
        )
        if deltas_ms:
            def _percentile(data: list[int], p: float) -> int:
                idx = max(0, int(len(data) * p / 100) - 1)
                return data[min(idx, len(data) - 1)]

            rsvp_p50_ms = _percentile(deltas_ms, 50)
            rsvp_p95_ms = _percentile(deltas_ms, 95)
            rsvp_sample_count = len(deltas_ms)
```

Update the `return` statement to include timing fields:

```python
    return AdminStatsResponse(
        active_users=active_users,
        total_events=total_events,
        events_pending=events_pending,
        events_processed=events_processed,
        events_failed=events_failed,
        last_discovery_at=last_discovery_at,
        recent_failures=recent_failures,
        rsvp_p50_ms=rsvp_p50_ms,
        rsvp_p95_ms=rsvp_p95_ms,
        rsvp_sample_count=rsvp_sample_count,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_admin_stats_api.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/admin.py tests/test_admin_stats_api.py
git commit -m "feat: compute RSVP timing p50/p95 in admin stats endpoint"
```

---

### Task 4: Add timing metrics to the admin panel stats bar

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: Add two new timing stat cards to the stats bar**

In `frontend/admin.html`, find the existing stats bar. Add these two cards after the "Last Discovery" card:

```html
          <div class="stat-card">
            <div class="stat-value" id="stat-p50" style="color: var(--accent)">—</div>
            <div class="stat-label">RSVP p50 (ms)</div>
          </div>
          <div class="stat-card">
            <div class="stat-value" id="stat-p95">—</div>
            <div class="stat-label">RSVP p95 (ms)</div>
          </div>
```

- [ ] **Step 2: Update the renderLastDiscovery / loadStats section to populate timing cards**

In the `loadStats()` function (already updated in the health-dashboard branch), add inside the try block after `renderRecentFailures(d.recent_failures)`:

```javascript
          document.getElementById('stat-p50').textContent =
            d.rsvp_p50_ms != null ? d.rsvp_p50_ms : '—';
          document.getElementById('stat-p95').textContent =
            d.rsvp_p95_ms != null ? d.rsvp_p95_ms : '—';
          const sampleEl = document.getElementById('stat-p50');
          if (sampleEl && d.rsvp_sample_count) {
            sampleEl.title = `Based on ${d.rsvp_sample_count} sample(s)`;
          }
```

- [ ] **Step 3: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: show RSVP timing p50/p95 in admin stats bar"
```

---

### Task 5: Add optional RSVP_LEAD_TIME_MS config and sniper offset

**Files:**
- Modify: `app/config.py`
- Modify: `app/workers/executioner.py`

This optional offset fires the sniper N milliseconds *before* `invite_time` to account for network round-trip. Set to 0 by default (no change in behavior).

- [ ] **Step 1: Write a failing test**

Add to `tests/test_executioner_log.py`:

```python
def test_lead_time_ms_default_zero():
    from app.config import settings
    assert settings.rsvp_lead_time_ms == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_executioner_log.py::test_lead_time_ms_default_zero -v
```

Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add rsvp_lead_time_ms to app/config.py**

In `app/config.py`, add inside the `Settings` class after `executioner_interval_seconds`:

```python
    # Fire RSVP this many milliseconds before invite_time to account for network latency.
    # 0 = fire at exactly invite_time (default). Increase if p50 consistently > 500ms.
    rsvp_lead_time_ms: int = 0
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_executioner_log.py::test_lead_time_ms_default_zero -v
```

Expected: PASS

- [ ] **Step 5: Apply the offset in schedule_sniper**

In `app/workers/executioner.py`, find the `schedule_sniper` function and add offset logic:

```python
def schedule_sniper(scheduler: AsyncIOScheduler, event: Event) -> None:
    """Schedule (or replace) a one-shot RSVP job at event.invite_time minus lead time."""
    from datetime import timedelta
    from app.config import settings

    now = datetime.now(timezone.utc)
    if not event.invite_time or event.invite_time <= now:
        return

    fire_at = event.invite_time - timedelta(milliseconds=settings.rsvp_lead_time_ms)
    if fire_at <= now:
        fire_at = now  # already past adjusted time — fire immediately

    job_id = _sniper_job_id(event.id)
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        run_sniper,
        trigger="date",
        run_date=fire_at,
        id=job_id,
        args=[event.id],
        misfire_grace_time=30,
    )
    logger.debug("Sniper scheduled for event %s at %s (lead=%dms)", event.id, fire_at, settings.rsvp_lead_time_ms)
```

- [ ] **Step 6: Run all tests to confirm nothing broke**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/workers/executioner.py tests/test_executioner_log.py
git commit -m "feat: add RSVP_LEAD_TIME_MS config for sniper offset"
```

---

### Task 6: Update docs and push

- [ ] **Step 1: Update docs/architecture.md config reference**

In `docs/architecture.md`, add to the configuration table:

```
| `RSVP_LEAD_TIME_MS` | no | `0` | Fire RSVP this many ms before invite_time (compensates for network latency) |
```

- [ ] **Step 2: Update CLAUDE.md config section**

In `CLAUDE.md`, add `RSVP_LEAD_TIME_MS` to the critical vars list.

- [ ] **Step 3: Run all tests one final time**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 4: Start the app and verify timing cards appear**

```bash
uvicorn app.main:app --reload --port 8080
```

Navigate to `/admin`. Confirm "RSVP p50 (ms)" and "RSVP p95 (ms)" cards appear in the stats bar (showing `—` until RSVPs have fired).

- [ ] **Step 5: Push the branch**

```bash
git push -u origin feat/observability-timing-metrics
```

---

## Spec Coverage Checklist

- [x] `submitted_at` in `rsvp_log` used as timing source (from audit-log branch)
- [x] Delta computed as `submitted_at - invite_time` in milliseconds
- [x] p50 and p95 computed over last 200 samples with both timestamps
- [x] `rsvp_p50_ms`, `rsvp_p95_ms`, `rsvp_sample_count` in `/admin/stats` response
- [x] Admin panel shows p50 and p95 stat cards
- [x] `RSVP_LEAD_TIME_MS` config var with 0 default (no behavior change)
- [x] Sniper offset applied when `RSVP_LEAD_TIME_MS > 0`
- [x] Docs updated
