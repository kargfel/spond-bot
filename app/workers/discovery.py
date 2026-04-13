"""
Worker A — Discovery Sync (runs every DISCOVERY_INTERVAL_MINUTES).

For each active user:
  1. Ensure the stored Spond token is fresh (re-login if needed).
  2. Fetch upcoming events from Spond.
  3. Fetch full event details via getBulk (to obtain inviteTime).
  4. Upsert events into the local DB without overwriting existing user choices.

New events are inserted with user_choice='manual' and status='pending',
so the executioner will not act on them until the user explicitly sets a choice.
"""
import logging
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import spond_client
from app.core.spond_client import SpondAuthError, parse_event_timestamps
from app.database import AsyncSessionLocal
from app.models.event import CHOICE_MANUAL, STATUS_PENDING, Event
from app.models.user import User
from app.services.auth import ensure_fresh_token

logger = logging.getLogger(__name__)

# Maximum IDs to send in a single getBulk request
_BULK_CHUNK_SIZE = 50


async def run_discovery() -> None:
    """Entry point called by APScheduler. Never raises — logs all errors."""
    logger.info("=== Discovery sync started ===")
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).where(User.is_active == True)  # noqa: E712
            )
            users = result.scalars().all()

        if not users:
            logger.info("No active users found. Discovery complete.")
            return

        logger.info("Syncing events for %d active user(s)...", len(users))

        for user in users:
            await _sync_user(user)

    except Exception as exc:
        logger.exception("Discovery sync crashed unexpectedly: %s", exc)

    logger.info("=== Discovery sync complete ===")


async def _sync_user(user: User) -> None:
    """Sync events for a single user. Errors are logged, not re-raised."""
    logger.info("Syncing user: %r", user.display_name)

    async with AsyncSessionLocal() as db:
        # Re-fetch user within the new session so it is session-bound
        db_user = await db.get(User, user.id)
        if not db_user:
            return

        try:
            token = await ensure_fresh_token(db, db_user)
        except SpondAuthError as exc:
            logger.error(
                "Auth failed for %r — skipping: %s", db_user.display_name, exc
            )
            return
        except Exception as exc:
            logger.error(
                "Token refresh error for %r: %s", db_user.display_name, exc
            )
            return

        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar()
            ) as http:
                # Step 1: get list of upcoming event IDs
                upcoming = await spond_client.get_upcoming_events(
                    http,
                    token,
                    include_declined=True,
                    min_end_ts=datetime.now(timezone.utc),
                )

                event_ids = [e["id"] for e in upcoming if e.get("id")]
                if not event_ids:
                    logger.info(
                        "No upcoming events for %r.", db_user.display_name
                    )
                    return

                # Step 2: get full details (inviteTime etc.) in chunks
                bulk: list[dict] = []
                for i in range(0, len(event_ids), _BULK_CHUNK_SIZE):
                    chunk = event_ids[i : i + _BULK_CHUNK_SIZE]
                    bulk.extend(
                        await spond_client.get_bulk_events(http, token, chunk)
                    )

        except SpondAuthError:
            # Force re-login next run
            db_user.encrypted_access_token = None
            db_user.token_acquired_at = None
            await db.commit()
            logger.warning(
                "401 during sync for %r — token cleared, will re-login next run.",
                db_user.display_name,
            )
            return
        except Exception as exc:
            logger.error(
                "API error syncing %r: %s", db_user.display_name, exc
            )
            return

        # Step 3: upsert events
        new_count = 0
        for raw in bulk:
            spond_id = raw.get("id")
            if not spond_id:
                continue

            timestamps = parse_event_timestamps(raw)
            now = datetime.now(timezone.utc)

            stmt = (
                pg_insert(Event)
                .values(
                    spond_event_id=spond_id,
                    user_id=db_user.id,
                    heading=raw.get("heading"),
                    user_choice=CHOICE_MANUAL,
                    status=STATUS_PENDING,
                    created_at=now,
                    updated_at=now,
                    **timestamps,
                )
                .on_conflict_do_update(
                    constraint="uq_event_user",
                    set_={
                        # Refresh metadata but never overwrite user decisions
                        "heading": raw.get("heading"),
                        "start_timestamp": timestamps["start_timestamp"],
                        "invite_time": timestamps["invite_time"],
                        "rsvp_date": timestamps["rsvp_date"],
                        "updated_at": now,
                    },
                )
            )
            result = await db.execute(stmt)
            if result.rowcount and result.inserted_primary_key:  # type: ignore
                new_count += 1

        await db.commit()
        logger.info(
            "User %r: synced %d event(s) (%d total from Spond).",
            db_user.display_name,
            new_count,
            len(bulk),
        )
