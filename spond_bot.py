import asyncio
import logging
import os
import random
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from spond import Spond

# [AUDIT FIX] Removed unused `import time` (audit: spond_bot.py:6)

load_dotenv()

# [AUDIT FIX] Replaced all raw print() calls with structured logging.
# This enables log levels (INFO/WARNING/ERROR/DEBUG), is compatible with
# container log aggregators, and can be silenced in tests without patching
# sys.stdout. (audit: Step 7 — Add logging module throughout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _get_int_env(name: str, default: int) -> int:
    """
    Read an integer environment variable with a safe fallback.

    [AUDIT FIX] Previously, int(os.getenv(...)) would raise an unhandled
    ValueError for non-numeric input (e.g. SPOND_POLL_TIMEOUT_MINUTES=abc).
    This helper catches that and falls back to the documented default.
    (audit: spond_bot.py L20-23)
    """
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid value %r for %s — must be an integer. Using default of %d.",
            raw, name, default,
        )
        return default


async def monitor_spond_events() -> None:
    spond_id = os.getenv("SPOND_ID")
    spond_user = os.getenv("SPOND_USERNAME")
    spond_pass = os.getenv("SPOND_PASSWORD")
    target_heading = os.getenv("SPOND_TARGET_EVENT_HEADING")

    # Load configurable timings via the validated helper (no crash on bad input)
    poll_timeout = _get_int_env("SPOND_POLL_TIMEOUT_MINUTES", 5)
    min_cooldown = _get_int_env("SPOND_MIN_COOLDOWN_SECONDS", 1)
    max_cooldown = _get_int_env("SPOND_MAX_COOLDOWN_SECONDS", 5)
    err_cooldown = _get_int_env("SPOND_ERROR_COOLDOWN_SECONDS", 20)

    attend_answer_raw = os.getenv("SPOND_ATTEND_ANSWER", "true").strip().lower()
    attend_answer = attend_answer_raw in ("true", "1", "yes", "y")

    if not spond_pass:
        logger.error("SPOND_PASSWORD not found in .env file")
        return
    if not spond_user:
        logger.error("SPOND_USERNAME not found in .env file")
        return
    if not target_heading:
        logger.error("SPOND_TARGET_EVENT_HEADING not found in .env file")
        return

    if not spond_id:
        logger.info("SPOND_ID not found in .env — attempting auto-discovery via API...")

        try:
            # [AUDIT FIX] Pass pre-validated credentials directly instead of having
            # get_spond_id() redundantly re-read env vars and create a new session
            # without context of the already-validated credentials. (audit: L43)
            spond_id = await get_spond_id(spond_user, spond_pass)
        except Exception as e:
            logger.error("Error fetching ID: %s", e)
            return

        if spond_id:
            logger.info("Successfully retrieved Spond ID: %s", spond_id)
        else:
            logger.error("Could not automatically find Spond ID for '%s'.", spond_user)
            return

    s = Spond(spond_user, spond_pass, spond_id)

    # [AUDIT FIX] Wrapped all bot logic in try/finally to guarantee the aiohttp
    # ClientSession is closed on EVERY exit path — including early returns (no event
    # found, threshold reached) and unhandled exceptions. Previously, the session was
    # only closed at the end of the happy path, leaking sockets on all other paths.
    # (audit: spond_bot.py L98)
    try:
        start_time = datetime.now(timezone.utc)
        threshold = timedelta(minutes=poll_timeout)
        events = None
        while events is None:
            try:
                events = await s.get_upcoming_events(
                    includeDeclined=True, minEndTimestamp=start_time
                )
            except Exception as e:
                logger.error("An error occurred: %s", e)
                cooldown = random.randint(min_cooldown, max_cooldown)
                logger.info("Cooling down for %d seconds.", cooldown)
                await asyncio.sleep(cooldown)

        # [AUDIT FIX] Initialise next_event_id to None BEFORE the loop.
        # Previously, if no event matched the heading, the variable was never assigned
        # and the subsequent `if nextEventID is None` raised a NameError, crashing the
        # bot. This is the most critical bug in the original code. (audit: L68-75)
        next_event_id = None
        for event in events:
            if event["heading"] == target_heading:
                next_event_id = event["id"]
                break

        if next_event_id is None:
            logger.warning(
                "No event with heading %r found in %d upcoming event(s).",
                target_heading, len(events),
            )
            return

        while True:
            if datetime.now(timezone.utc) > start_time + threshold:
                logger.info("Poll timeout reached (%d min). Exiting.", poll_timeout)
                break
            try:
                response = await s.give_answer(next_event_id, attend_answer)
                logger.info("Answer submitted successfully. Response: %s", response)
                break

            except Exception as e:
                error_msg = str(e)
                logger.warning("Attempt failed: %s", error_msg)

                # [AUDIT FIX] Detect 403 by matching the structured ValueError message
                # produced by spond.py ("status 403") rather than searching the whole
                # error string for "403", which would falsely match URLs or other data
                # containing that digit sequence. (audit: L89)
                if "status 403" in error_msg:
                    cooldown = random.randint(min_cooldown, max_cooldown)
                    logger.info(
                        "Rate-limited (403 Forbidden). Cooling down for %d seconds.",
                        cooldown,
                    )
                    await asyncio.sleep(cooldown)
                else:
                    cooldown = random.randint(err_cooldown, err_cooldown + 20)
                    logger.info(
                        "Unknown error. Cooling down for %d seconds.", cooldown
                    )
                    await asyncio.sleep(cooldown)

    finally:
        await s.clientsession.close()


async def get_spond_id(username: str, password: str) -> str | None:
    """
    Auto-discover the caller's Spond member ID by scanning group memberships.

    [AUDIT FIX] Accepts pre-validated credentials as parameters instead of
    re-reading os.getenv() internally. This removes the hidden coupling to the
    environment, makes the function testable in isolation, and avoids a second
    redundant Spond/ClientSession construction inside the main flow. (audit: L43)

    The temporary ClientSession is now closed in a finally block so it is
    guaranteed to close even if get_groups() raises. (audit: L98 pattern)
    """
    s = Spond(username, password, "")
    try:
        groups = await s.get_groups()
    except Exception as e:
        logger.error("Failed to fetch groups: %s", e)
        return None
    finally:
        # Close the temporary session used solely for ID discovery
        await s.clientsession.close()

    if not groups:
        return None

    target = username.strip().lower()
    for group in groups:
        for member in group.get("members", []):
            member_email = (member.get("email") or "").strip().lower()
            profile_email = (member.get("profile", {}).get("email") or "").strip().lower()

            member_phone = (member.get("phoneNumber") or "").strip().lower()
            profile_phone = (member.get("profile", {}).get("phoneNumber") or "").strip().lower()

            if target and target in (member_email, profile_email, member_phone, profile_phone):
                return member["id"]
    return None


async def main() -> None:
    await monitor_spond_events()


if __name__ == "__main__":
    asyncio.run(main())