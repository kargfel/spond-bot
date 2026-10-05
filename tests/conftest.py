import os

# Settings are read when `app` is first imported, so test-safe values must be in
# place before that. Real env vars win over .env, which keeps tests away from a
# developer's real database and credentials. The engine is created lazily
# (no connection is made); every test swaps in SQLite via get_db overrides.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("FERNET_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
# Audit rows go to Postgres through their own session; tests that care opt in (tests/test_audit_*.py).
os.environ.setdefault("AUDIT_ENABLED", "false")

import pytest  # noqa: E402
from httpx import AsyncClient, ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, get_db  # noqa: E402
from app.api import deps  # noqa: E402
import app.models.rsvp_log  # noqa: F401 — ensures rsvp_log table is registered in Base.metadata
import app.models.user  # noqa: F401
import app.models.event  # noqa: F401
import app.models.invite  # noqa: F401
import app.models.push_subscription  # noqa: F401
import app.models.audit_log  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


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
        await session.rollback()


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


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate limits are per client IP and kept in memory; start every test clean."""
    from app.core.rate_limit import limiter

    limiter.reset()
    yield


@pytest.fixture
def client_as(test_db):
    """
    Factory for an API client with a given session (claims dict), or anonymous
    when claims is None. Usage: `async with client_as({...}) as client:`.
    """
    from contextlib import asynccontextmanager

    from app.main import app

    @asynccontextmanager
    async def _make(claims: dict | None):
        async def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db
        if claims is not None:
            async def override_current_user():
                return claims

            app.dependency_overrides[deps._get_current_user] = override_current_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                yield client
        finally:
            app.dependency_overrides.clear()

    return _make


@pytest.fixture
def spond_api():
    """Patch the Spond login used when verifying credentials. Yields the login mock."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch

    with patch("app.services.spond_accounts.spond_client.login", new_callable=AsyncMock) as login, \
         patch("app.services.spond_accounts.spond_client.get_profile_id", new_callable=AsyncMock) as profile:
        login.return_value = ("spond-token", datetime.now(timezone.utc))
        profile.return_value = "PROFILE-123"
        yield login


@pytest.fixture
def real_sessions(monkeypatch, test_db):
    """Let real session cookies be checked against the test database (see deps._get_current_user)."""
    from contextlib import asynccontextmanager

    from app.api import deps

    @asynccontextmanager
    async def session():
        yield test_db

    monkeypatch.setattr(deps, "open_session", session)


@pytest.fixture
def audit_on(monkeypatch, test_db, real_sessions):
    """Turn the audit trail on and write its rows into the test database."""
    from contextlib import asynccontextmanager

    from app.config import settings
    from app.services import audit

    monkeypatch.setattr(settings, "audit_enabled", True)

    @asynccontextmanager
    async def session():
        yield test_db

    monkeypatch.setattr(audit, "open_session", session)
    return audit
