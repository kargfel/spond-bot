"""Alembic environment — async SQLAlchemy support."""
import asyncio
import os
import sys
from pathlib import Path

# Alembic executes this file as a standalone script, so the project root
# (/app inside Docker) is not automatically on sys.path. Add it explicitly
# so that `from app.xxx import ...` imports resolve correctly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Load all models so Alembic can detect schema changes via autogenerate
from app.database import Base
from app.models import user, event, frontend_user, rsvp_log  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment so the alembic.ini file
# never contains a hardcoded connection string
database_url = os.environ["DATABASE_URL"]
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


run_migrations_online()
