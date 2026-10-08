import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import database


class FakeConn:
    opened = 0
    peak = 0
    live = 0

    async def __aenter__(self):
        FakeConn.opened += 1
        FakeConn.live += 1
        FakeConn.peak = max(FakeConn.peak, FakeConn.live)
        return self

    async def __aexit__(self, *a):
        FakeConn.live -= 1

    execute = AsyncMock()


class FakeEngine:
    def __init__(self, error=None):
        self.error = error

    def connect(self):
        if self.error:
            raise self.error
        return FakeConn()


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    FakeConn.opened = FakeConn.peak = FakeConn.live = 0
    database._last_warm = 0.0
    monkeypatch.setattr(database, "engine", FakeEngine())


@pytest.mark.asyncio
async def test_warm_pool_holds_all_connections_at_once():
    await database.warm_pool(8)
    assert FakeConn.opened == 8 and FakeConn.peak == 8


@pytest.mark.asyncio
async def test_warm_pool_never_asks_for_more_than_the_pool_size():
    with patch.object(database.settings, "db_pool_size", 5):
        await database.warm_pool(50)
    assert FakeConn.opened == 5


@pytest.mark.asyncio
async def test_warm_pool_rate_limit_and_failures():
    await database.warm_pool(3, min_interval_s=30)
    await database.warm_pool(3, min_interval_s=30)   # skipped
    assert FakeConn.opened == 3
    database.engine = FakeEngine(OSError("db down"))
    await database.warm_pool(3)                       # must not raise


def test_pool_is_sized_from_settings(monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    monkeypatch.undo()
    engine = create_async_engine(database.settings.database_url, pool_size=database.settings.db_pool_size,
                                 max_overflow=database.settings.db_max_overflow)
    assert engine.pool.size() == 30 and database.settings.db_max_overflow == 20
