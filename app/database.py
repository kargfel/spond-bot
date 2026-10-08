"""
Async SQLAlchemy engine, session factory, and declarative base.

All ORM models import `Base` from here. Workers and API endpoints
access the DB via the `get_db` async generator.
"""
import asyncio
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,  # detect and drop stale connections
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency: yields a DB session and always closes it."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


_last_warm = 0.0


async def warm_pool(connections: int | None = None, *, min_interval_s: float = 0.0) -> None:
    """Make sure `connections` (default: the pool size) are open and healthy, idle in the pool.

    Opening a Postgres connection costs tens of milliseconds of CPU on the event loop; doing it for
    a dozen members at the instant registration opens is what delayed answers. Holding them all at
    once forces the pool to create the missing ones now. Never raises.
    """
    global _last_warm
    if min_interval_s and time.monotonic() - _last_warm < min_interval_s:
        return
    _last_warm = time.monotonic()
    n = min(connections or settings.db_pool_size, settings.db_pool_size)

    async def hold() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await asyncio.sleep(0.05)

    await asyncio.gather(*(hold() for _ in range(n)), return_exceptions=True)
