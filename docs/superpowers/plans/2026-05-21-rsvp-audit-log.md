# RSVP Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every RSVP attempt (success, retry, failure) in a persistent `rsvp_log` table and expose it via a paginated admin-only API endpoint and admin panel view.

**Architecture:** A new `RsvpLog` SQLAlchemy model captures who fired the RSVP, when, what choice, outcome, and timing. `_process_event()` in the executioner writes a row on every outcome path. A new `app/api/admin.py` router exposes `GET /api/v1/admin/rsvp-log` (admin only). The admin panel gains a new "RSVP Log" view.

**Tech Stack:** SQLAlchemy async ORM, Alembic migrations, FastAPI, pytest + pytest-asyncio + httpx + aiosqlite (tests), vanilla JS (frontend).

**Feature branch:** `feat/observability-rsvp-audit-log`

---

### Task 1: Create feature branch and add test dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Create and checkout the feature branch**

```bash
git checkout -b feat/observability-rsvp-audit-log
```

- [ ] **Step 2: Add test dependencies to requirements.txt**

Append these lines to `requirements.txt`:

```
# Testing
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
aiosqlite>=0.20.0
```

- [ ] **Step 3: Install them**

```bash
pip install pytest pytest-asyncio httpx aiosqlite
```

- [ ] **Step 4: Create pytest config in pyproject.toml (create if missing) or pytest.ini**

Create `pytest.ini` in the project root:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pytest.ini
git commit -m "chore: add test dependencies and pytest config"
```

---

### Task 2: Create test infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create tests directory and empty __init__**

```bash
mkdir tests
touch tests/__init__.py
```

- [ ] **Step 2: Write conftest.py**

Create `tests/conftest.py`:

```python
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, get_db
from app.api import deps

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def test_db(test_engine):
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def admin_client(test_db):
    from app.main import app

    async def override_get_db():
        yield test_db

    async def override_current_user():
        return {
            "sub": "00000000-0000-0000-0000-000000000001",
            "username": "admin",
            "is_admin": True,
            "linked_user_id": None,
        }

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps._get_current_user] = override_current_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()
```

- [ ] **Step 3: Verify conftest imports correctly (no crash)**

```bash
python -c "import tests.conftest; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: add pytest conftest with SQLite test DB and admin client fixture"
```

---

### Task 3: Create RsvpLog model

**Files:**
- Create: `app/models/rsvp_log.py`
- Modify: `migrations/env.py`

- [ ] **Step 1: Write a failing import test**

Create `tests/test_rsvp_log_model.py`:

```python
def test_rsvp_log_model_importable():
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS, OUTCOME_RETRY_SUCCESS, OUTCOME_FAILED
    assert RsvpLog.__tablename__ == "rsvp_log"
    assert OUTCOME_SUCCESS == "success"
    assert OUTCOME_RETRY_SUCCESS == "retry_success"
    assert OUTCOME_FAILED == "failed"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_rsvp_log_model.py -v
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create app/models/rsvp_log.py**

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

OUTCOME_SUCCESS = "success"
OUTCOME_RETRY_SUCCESS = "retry_success"
OUTCOME_FAILED = "failed"


class RsvpLog(Base):
    """
    Immutable audit record of every RSVP attempt.

    Written by _process_event() in the executioner on every outcome:
    success, retry_success, or failed. Never updated after insert.
    """

    __tablename__ = "rsvp_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SET NULL on delete so log rows survive event/user deletion
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    spond_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    choice: Mapped[str] = mapped_column(String(10), nullable=False)
    # When _process_event() began processing this event
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the actual PUT /responses request was dispatched (None if never reached)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "success" | "retry_success" | "failed"
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_rsvp_log_model.py -v
```

Expected: PASS

- [ ] **Step 5: Add rsvp_log import to migrations/env.py**

In `migrations/env.py`, find this line:
```python
from app.models import user, event, frontend_user  # noqa: F401
```
Replace it with:
```python
from app.models import user, event, frontend_user, rsvp_log  # noqa: F401
```

- [ ] **Step 6: Generate the Alembic migration**

```bash
alembic revision --autogenerate -m "add_rsvp_log_table"
```

Expected: Creates a new file in `migrations/versions/` with `op.create_table("rsvp_log", ...)`

- [ ] **Step 7: Review the generated migration**

Open the generated file and confirm it contains `op.create_table("rsvp_log", ...)` with all expected columns. If anything looks wrong, edit the migration file before continuing.

- [ ] **Step 8: Commit**

```bash
git add app/models/rsvp_log.py migrations/env.py migrations/versions/
git commit -m "feat: add RsvpLog model and Alembic migration"
```

---

### Task 4: Create Pydantic schema

**Files:**
- Create: `app/schemas/rsvp_log.py`

- [ ] **Step 1: Write a failing schema test**

Create `tests/test_rsvp_log_schema.py`:

```python
import uuid
from datetime import datetime, timezone

def test_rsvp_log_response_schema():
    from app.schemas.rsvp_log import RsvpLogResponse
    data = {
        "id": uuid.uuid4(),
        "event_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "spond_event_id": "ABC123",
        "choice": "accept",
        "fired_at": datetime.now(timezone.utc),
        "submitted_at": datetime.now(timezone.utc),
        "outcome": "success",
        "retry_count": 0,
        "error_detail": None,
    }
    obj = RsvpLogResponse(**data)
    assert obj.choice == "accept"
    assert obj.outcome == "success"

def test_rsvp_log_response_nullable_fields():
    from app.schemas.rsvp_log import RsvpLogResponse
    import uuid
    from datetime import datetime, timezone
    obj = RsvpLogResponse(
        id=uuid.uuid4(),
        event_id=None,
        user_id=None,
        spond_event_id="XYZ",
        choice="decline",
        fired_at=datetime.now(timezone.utc),
        submitted_at=None,
        outcome="failed",
        retry_count=1,
        error_detail="Network timeout",
    )
    assert obj.event_id is None
    assert obj.submitted_at is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_rsvp_log_schema.py -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Create app/schemas/rsvp_log.py**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class RsvpLogResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None
    user_id: uuid.UUID | None
    spond_event_id: str
    choice: str
    fired_at: datetime
    submitted_at: datetime | None
    outcome: str
    retry_count: int
    error_detail: str | None

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_rsvp_log_schema.py -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/rsvp_log.py tests/test_rsvp_log_schema.py
git commit -m "feat: add RsvpLogResponse Pydantic schema"
```

---

### Task 5: Add _write_rsvp_log helper to executioner

**Files:**
- Modify: `app/workers/executioner.py`

- [ ] **Step 1: Write a failing test for the helper**

Add to a new file `tests/test_executioner_log.py`:

```python
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_write_rsvp_log_adds_row():
    from app.workers.executioner import _write_rsvp_log
    from app.models.rsvp_log import OUTCOME_SUCCESS

    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.spond_event_id = "EVT001"
    mock_event.user_choice = "accept"

    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()

    fired_at = datetime.now(timezone.utc)
    submitted_at = datetime.now(timezone.utc)

    await _write_rsvp_log(
        mock_db, mock_event, mock_user, fired_at, submitted_at, OUTCOME_SUCCESS, 0
    )

    mock_db.add.assert_called_once()
    log_row = mock_db.add.call_args[0][0]
    assert log_row.spond_event_id == "EVT001"
    assert log_row.choice == "accept"
    assert log_row.outcome == OUTCOME_SUCCESS
    assert log_row.retry_count == 0
    assert log_row.error_detail is None


@pytest.mark.asyncio
async def test_write_rsvp_log_none_user():
    from app.workers.executioner import _write_rsvp_log
    from app.models.rsvp_log import OUTCOME_FAILED

    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.spond_event_id = "EVT002"
    mock_event.user_choice = "accept"

    fired_at = datetime.now(timezone.utc)

    await _write_rsvp_log(
        mock_db, mock_event, None, fired_at, None, OUTCOME_FAILED, 0, "User not found"
    )

    log_row = mock_db.add.call_args[0][0]
    assert log_row.user_id is None
    assert log_row.error_detail == "User not found"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_executioner_log.py -v
```

Expected: FAIL with `ImportError` — `_write_rsvp_log` does not exist yet

- [ ] **Step 3: Add _write_rsvp_log to executioner.py**

In `app/workers/executioner.py`, add this import at the top (with the existing imports):

```python
from app.models.rsvp_log import OUTCOME_FAILED, OUTCOME_RETRY_SUCCESS, OUTCOME_SUCCESS, RsvpLog
```

Then add this function after the existing imports and before `run_executioner()`:

```python
async def _write_rsvp_log(
    db: AsyncSession,
    event: Event,
    user: "User | None",
    fired_at: datetime,
    submitted_at: "datetime | None",
    outcome: str,
    retry_count: int,
    error_detail: "str | None" = None,
) -> None:
    """Append an immutable audit row for this RSVP attempt. Caller must commit."""
    log = RsvpLog(
        event_id=event.id,
        user_id=user.id if user else None,
        spond_event_id=event.spond_event_id,
        choice=event.user_choice,
        fired_at=fired_at,
        submitted_at=submitted_at,
        outcome=outcome,
        retry_count=retry_count,
        error_detail=error_detail,
    )
    db.add(log)
```

Also add `"User | None"` to the TYPE_CHECKING imports — in the existing `if TYPE_CHECKING:` block (add `User` if not already imported). Since `User` is already imported at the top of executioner.py for `_process_event`, no change needed.

Add `datetime` to the existing imports at the top if not already present:
```python
from datetime import datetime, timezone
```
(It is already imported — no change needed.)

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_executioner_log.py -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/workers/executioner.py tests/test_executioner_log.py
git commit -m "feat: add _write_rsvp_log helper to executioner"
```

---

### Task 6: Modify _submit_rsvp to return submitted_at and wire log calls into _process_event

**Files:**
- Modify: `app/workers/executioner.py`

- [ ] **Step 1: Update _submit_rsvp return type**

In `app/workers/executioner.py`, replace the `_submit_rsvp` function signature and body:

```python
async def _submit_rsvp(
    db: AsyncSession,
    user: User,
    spond_event_id: str,
    accepted: bool,
    *,
    force_refresh: bool = False,
) -> datetime:
    """Obtain a fresh token, resolve recipient ID, fire the RSVP. Returns submitted_at."""
    token = await ensure_fresh_token(db, user, force=force_refresh)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as http:
        bulk = await spond_client.get_bulk_events(http, token, [spond_event_id])
        if not bulk:
            raise SpondAPIError(f"Event {spond_event_id} not found on Spond server")

        raw_event = bulk[0]

        recipient_id = await spond_client.resolve_recipient_id(
            http, token, raw_event, user.login, user.profile_id  # type: ignore[arg-type]
        )

        logger.info(
            "RSVP recipient resolved: user=%r event=%s recipient_id=%s (profile_id=%s)",
            user.display_name, spond_event_id, recipient_id, user.profile_id,
        )

        submitted_at = datetime.now(timezone.utc)
        await spond_client.rsvp(http, token, spond_event_id, recipient_id, accepted)
        return submitted_at
```

- [ ] **Step 2: Replace _process_event with log-wired version**

Replace the entire `_process_event` function body in `app/workers/executioner.py`:

```python
async def _process_event(event: Event) -> None:
    """Handle a single RSVP submission with one automatic retry on 401."""
    fired_at = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        db_event = await db.get(Event, event.id)
        if not db_event or db_event.status != STATUS_PENDING:
            return

        user = await db.get(User, db_event.user_id)
        if not user or not user.profile_id:
            logger.error(
                "Event %s has no resolvable user or profile_id — skipping.",
                db_event.id,
            )
            db_event.status = STATUS_FAILED
            db_event.error_message = "User not found or missing profile_id."
            await _write_rsvp_log(
                db, db_event, None, fired_at, None, OUTCOME_FAILED, 0,
                "User not found or missing profile_id.",
            )
            await db.commit()
            return

        db_event.status = STATUS_PROCESSING
        await db.commit()

        accepted = db_event.user_choice == CHOICE_ACCEPT

        try:
            submitted_at = await _submit_rsvp(db, user, db_event.spond_event_id, accepted)
            db_event.status = STATUS_PROCESSED
            db_event.error_message = None
            await _write_rsvp_log(db, db_event, user, fired_at, submitted_at, OUTCOME_SUCCESS, 0)
            logger.info(
                "RSVP %s for %r (%r) → SUCCESS",
                "ACCEPT" if accepted else "DECLINE",
                user.display_name,
                db_event.heading,
            )
        except SpondAuthError:
            logger.warning(
                "401 on RSVP for %r — forcing token refresh and retrying.",
                user.display_name,
            )
            try:
                submitted_at = await _submit_rsvp(
                    db, user, db_event.spond_event_id, accepted, force_refresh=True
                )
                db_event.status = STATUS_PROCESSED
                db_event.error_message = None
                await _write_rsvp_log(
                    db, db_event, user, fired_at, submitted_at, OUTCOME_RETRY_SUCCESS, 1
                )
                logger.info(
                    "RSVP %s for %r (%r) → SUCCESS (after retry)",
                    "ACCEPT" if accepted else "DECLINE",
                    user.display_name,
                    db_event.heading,
                )
            except Exception as retry_exc:
                db_event.status = STATUS_FAILED
                db_event.error_message = f"Retry failed: {retry_exc}"
                await _write_rsvp_log(
                    db, db_event, user, fired_at, None, OUTCOME_FAILED, 1, str(retry_exc)
                )
                logger.error(
                    "RSVP failed for %r (%r) after retry: %s",
                    user.display_name,
                    db_event.heading,
                    retry_exc,
                )
        except SpondAPIError as exc:
            db_event.status = STATUS_FAILED
            db_event.error_message = str(exc)
            await _write_rsvp_log(
                db, db_event, user, fired_at, None, OUTCOME_FAILED, 0, str(exc)
            )
            logger.error(
                "RSVP API error for %r (%r): %s",
                user.display_name,
                db_event.heading,
                exc,
            )
        except Exception as exc:
            db_event.status = STATUS_FAILED
            db_event.error_message = f"Unexpected error: {exc}"
            await _write_rsvp_log(
                db, db_event, user, fired_at, None, OUTCOME_FAILED, 0,
                f"Unexpected error: {exc}",
            )
            logger.exception(
                "Unexpected RSVP error for %r (%r): %s",
                user.display_name,
                db_event.heading,
                exc,
            )

        await db.commit()
```

- [ ] **Step 3: Run all existing tests to confirm nothing broke**

```bash
pytest tests/ -v
```

Expected: All previously passing tests still PASS

- [ ] **Step 4: Commit**

```bash
git add app/workers/executioner.py
git commit -m "feat: wire _write_rsvp_log into _process_event on all outcome paths"
```

---

### Task 7: Create admin router with GET /admin/rsvp-log

**Files:**
- Create: `app/api/admin.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_admin_rsvp_log_api.py`:

```python
import uuid
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_rsvp_log_empty(admin_client, test_db):
    resp = await admin_client.get("/api/v1/admin/rsvp-log")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_rsvp_log_returns_entries(admin_client, test_db):
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS

    log = RsvpLog(
        id=uuid.uuid4(),
        event_id=None,
        user_id=None,
        spond_event_id="EVT001",
        choice="accept",
        fired_at=datetime.now(timezone.utc),
        submitted_at=datetime.now(timezone.utc),
        outcome=OUTCOME_SUCCESS,
        retry_count=0,
        error_detail=None,
    )
    test_db.add(log)
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/rsvp-log")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["spond_event_id"] == "EVT001"
    assert data[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_rsvp_log_limit(admin_client, test_db):
    from app.models.rsvp_log import RsvpLog, OUTCOME_SUCCESS

    for i in range(5):
        test_db.add(RsvpLog(
            id=uuid.uuid4(),
            event_id=None,
            user_id=None,
            spond_event_id=f"EVT{i:03d}",
            choice="accept",
            fired_at=datetime.now(timezone.utc),
            submitted_at=None,
            outcome=OUTCOME_SUCCESS,
            retry_count=0,
        ))
    await test_db.commit()

    resp = await admin_client.get("/api/v1/admin/rsvp-log?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_rsvp_log_requires_admin(test_db):
    from app.main import app
    from app.database import get_db
    from app.api import deps
    from httpx import AsyncClient, ASGITransport

    async def override_get_db():
        yield test_db

    async def non_admin_user():
        return {"sub": "abc", "username": "user", "is_admin": False, "linked_user_id": None}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps._get_current_user] = non_admin_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/rsvp-log")

    app.dependency_overrides.clear()
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_admin_rsvp_log_api.py -v
```

Expected: FAIL with 404 (router not registered yet)

- [ ] **Step 3: Create app/api/admin.py**

```python
"""
/api/v1/admin — Admin-only observability endpoints.

All endpoints require is_admin == True (enforced via AdminDep).

GET /admin/rsvp-log        Paginated RSVP audit log
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, DbDep
from app.models.rsvp_log import RsvpLog
from app.schemas.rsvp_log import RsvpLogResponse

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get(
    "/rsvp-log",
    response_model=list[RsvpLogResponse],
    dependencies=[AdminDep],
    summary="RSVP audit log (admin only)",
)
async def get_rsvp_log(
    db: AsyncSession = DbDep,
    user_id: uuid.UUID | None = Query(None, description="Filter by Spond user UUID"),
    since: datetime | None = Query(None, description="Return only entries fired after this UTC timestamp"),
    limit: int = Query(100, le=500, description="Maximum rows to return"),
):
    """
    Returns RSVP attempt records in reverse-chronological order.
    Each row captures who fired the RSVP, when, the outcome, and any error.
    """
    q = select(RsvpLog).order_by(RsvpLog.fired_at.desc()).limit(limit)
    if user_id:
        q = q.where(RsvpLog.user_id == user_id)
    if since:
        q = q.where(RsvpLog.fired_at >= since)
    result = await db.execute(q)
    return result.scalars().all()
```

- [ ] **Step 4: Register the admin router in app/main.py**

In `app/main.py`, add after the existing router imports:

```python
from app.api import admin as admin_router
```

And after the existing `app.include_router(...)` calls:

```python
app.include_router(admin_router.router, prefix="/api/v1")
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_admin_rsvp_log_api.py -v
```

Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/admin.py app/main.py tests/test_admin_rsvp_log_api.py
git commit -m "feat: add GET /api/v1/admin/rsvp-log endpoint (admin only)"
```

---

### Task 8: Add RSVP Log view to admin panel

**Files:**
- Modify: `frontend/admin.html`

- [ ] **Step 1: Add sidebar nav button**

In `frontend/admin.html`, find the sidebar `<nav class="sidebar-nav" id="admin-nav">` block. Add this button after the Users button:

```html
          <button
            class="sidebar-item"
            data-view="rsvp-log"
            onclick="switchView('rsvp-log', this)"
          >
            ${ICON.shield} RSVP Log
          </button>
```

- [ ] **Step 2: Add the RSVP Log view div**

In `frontend/admin.html`, after the closing `</div>` of `<!-- ── Users View ───── -->` (before `</main>`), add:

```html
        <!-- ── RSVP Log View ─────────────────────────────────────── -->
        <div id="view-rsvp-log" class="admin-section hidden">
          <div class="page-header" style="margin-bottom: 1rem">
            <h1 class="page-title">RSVP Audit Log</h1>
            <button class="btn btn-outline btn-sm" onclick="loadRsvpLog()">
              ${ICON.sync} Refresh
            </button>
          </div>
          <div class="filter-bar mb-2">
            <select id="rl-user" class="input" onchange="loadRsvpLog()">
              <option value="">All Users</option>
            </select>
            <select id="rl-outcome" class="input" onchange="loadRsvpLog()">
              <option value="">All Outcomes</option>
              <option value="success">Success</option>
              <option value="retry_success">Retry Success</option>
              <option value="failed">Failed</option>
            </select>
          </div>
          <div id="rsvp-log-wrap">
            <div class="flex-center" style="padding: 2.5rem">
              <span class="spinner" style="width: 22px; height: 22px; border-width: 3px"></span>
            </div>
          </div>
        </div>
```

- [ ] **Step 3: Update mobile nav (optional — skip if mobile nav is already full)**

In the `<nav class="mobile-nav">` section, no changes needed — the RSVP log is desktop/sidebar only for now (it's an admin debug tool, not a primary mobile view).

- [ ] **Step 4: Add JS functions in the inline script block**

In the `<script>` block at the bottom of `admin.html`, add these functions after the `doSync()` function:

```javascript
      // ── RSVP Audit Log ─────────────────────────────────────────────────
      async function loadRsvpLog() {
        const wrap = document.getElementById('rsvp-log-wrap');
        wrap.innerHTML = `<div class="flex-center" style="padding:2.5rem"><span class="spinner" style="width:22px;height:22px;border-width:3px;"></span></div>`;

        const userId = document.getElementById('rl-user')?.value || '';
        const outcome = document.getElementById('rl-outcome')?.value || '';

        let url = '/admin/rsvp-log?limit=200';
        if (userId) url += `&user_id=${userId}`;

        try {
          const res = await api(url);
          if (!res.ok) throw new Error('Failed to fetch RSVP log');
          let rows = await res.json();

          if (outcome) rows = rows.filter(r => r.outcome === outcome);

          renderRsvpLog(rows);
        } catch (err) {
          wrap.innerHTML = `<div class="empty-state"><div class="empty-state-title">Failed to load log</div><div class="empty-state-sub">${escHtml(err.message)}</div></div>`;
        }
      }

      function renderRsvpLog(rows) {
        const wrap = document.getElementById('rsvp-log-wrap');
        if (!rows.length) {
          wrap.innerHTML = `<div class="empty-state"><div class="empty-state-title">No RSVP log entries</div><div class="empty-state-sub">Entries appear here after the bot fires its first RSVP.</div></div>`;
          return;
        }

        const outcomeBadge = (o) => {
          if (o === 'success') return `<span class="badge badge-accept">success</span>`;
          if (o === 'retry_success') return `<span class="badge badge-manual" style="background:rgba(255,180,0,0.15);color:#ffb400;border-color:rgba(255,180,0,0.3)">retry ok</span>`;
          return `<span class="badge badge-decline">failed</span>`;
        };

        wrap.innerHTML = `
          <div style="overflow-x:auto">
            <table style="width:100%;border-collapse:collapse;font-size:0.82rem">
              <thead>
                <tr style="border-bottom:1px solid var(--border);color:var(--text-muted)">
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">Fired At</th>
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">User</th>
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">Event ID</th>
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">Choice</th>
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">Outcome</th>
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">Retries</th>
                  <th style="text-align:left;padding:0.5rem 0.75rem;font-weight:600">Error</th>
                </tr>
              </thead>
              <tbody>
                ${rows.map(r => `
                  <tr style="border-bottom:1px solid var(--surface-container-high)">
                    <td style="padding:0.5rem 0.75rem;white-space:nowrap;color:var(--text-muted)">${fmtDate(r.fired_at)}</td>
                    <td style="padding:0.5rem 0.75rem">${escHtml(getUserName(r.user_id))}</td>
                    <td style="padding:0.5rem 0.75rem;font-family:monospace;font-size:0.75rem;color:var(--text-muted)">${escHtml(r.spond_event_id.slice(0, 12))}…</td>
                    <td style="padding:0.5rem 0.75rem">${choiceBadge(r.choice)}</td>
                    <td style="padding:0.5rem 0.75rem">${outcomeBadge(r.outcome)}</td>
                    <td style="padding:0.5rem 0.75rem;text-align:center">${r.retry_count}</td>
                    <td style="padding:0.5rem 0.75rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--status-red);font-size:0.75rem" title="${escHtml(r.error_detail || '')}">${escHtml(r.error_detail || '—')}</td>
                  </tr>`).join('')}
              </tbody>
            </table>
          </div>`;
      }
```

- [ ] **Step 5: Update switchView to handle 'rsvp-log'**

Find the `switchView` function in the `<script>` block and replace it:

```javascript
      function switchView(view, btn) {
        document.getElementById('view-events').classList.toggle('hidden', view !== 'events');
        document.getElementById('view-users').classList.toggle('hidden', view !== 'users');
        document.getElementById('view-rsvp-log').classList.toggle('hidden', view !== 'rsvp-log');
        document.querySelectorAll('.sidebar-item').forEach(s => s.classList.remove('active'));
        if (btn) btn.classList.add('active');
        if (view === 'users') loadUsersView();
        if (view === 'rsvp-log') loadRsvpLog();
      }
```

- [ ] **Step 6: Populate rl-user dropdown from allSpondUsers**

In the `loadSpondUsers()` function, after the existing loop that populates `#new-linked-user`, add:

```javascript
          // Also populate RSVP log user filter
          const rlSel = document.getElementById('rl-user');
          if (rlSel) {
            rlSel.innerHTML = '<option value="">All Users</option>';
            allSpondUsers.forEach(u => {
              const opt = document.createElement('option');
              opt.value = u.id;
              opt.textContent = u.display_name;
              rlSel.appendChild(opt);
            });
          }
```

- [ ] **Step 7: Fix the sidebar icon injection in the init script block**

Find the block at the bottom of admin.html that re-injects icons into `[data-view]` elements. Update the arrays to include rsvp-log:

```javascript
      document.querySelectorAll("[data-view]").forEach((el) => {
        const icons = [ICON.events, ICON.user, ICON.shield];
        const labels = [" Events", " Users", " RSVP Log"];
        const i = ["events", "users", "rsvp-log"].indexOf(
          el.dataset.view ?? el.getAttribute("onclick")?.match(/'(\w+)'/)?.[1],
        );
        if (i >= 0) el.innerHTML = icons[i] + labels[i];
      });
```

- [ ] **Step 8: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add frontend/admin.html
git commit -m "feat: add RSVP Audit Log view to admin panel"
```

---

### Task 9: Final integration check and push

- [ ] **Step 1: Run all tests one final time**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 2: Verify the app starts without errors**

```bash
uvicorn app.main:app --reload --port 8080
```

Navigate to `http://localhost:8080/admin`, log in, confirm the RSVP Log sidebar item appears and the view loads without JS errors.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/observability-rsvp-audit-log
```

---

## Spec Coverage Checklist

- [x] `rsvp_log` table with all required columns (event_id, user_id, choice, fired_at, submitted_at, outcome, retry_count, error_detail)
- [x] Log row written on success, retry success, and failure
- [x] Log row written even when user not found (user_id=NULL)
- [x] `GET /api/v1/admin/rsvp-log` with user_id, since, limit filters
- [x] Admin-only enforcement (403 for non-admin)
- [x] Admin panel view with user + outcome filters
- [x] Alembic migration for the new table
