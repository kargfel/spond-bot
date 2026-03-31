import asyncio
import logging
import os
import random
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from spond import Spond

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _get_int_env(name: str, default: int) -> int:
    """
    Read an integer environment variable, falling back to `default` on invalid input.

    Logs a warning if the value cannot be parsed so misconfigured variables are
    visible in the container logs rather than causing an unhandled crash.
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

    # The session is closed in a finally block to guarantee cleanup on every exit
    # path: normal completion, no matching event found, timeout, or raised exception.
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

                # Match against the structured error message raised by give_answer()
                # (e.g. "Request failed with status 403: ...") to avoid false positives
                # from the digit sequence "403" appearing elsewhere in the error string.
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

    Searches all groups the authenticated user belongs to, matching members by
    email address or phone number against the provided `username`. Returns the
    member ID on the first match, or None if no match is found.

    A dedicated Spond session is created for the lookup and is always closed
    on exit, regardless of whether the request succeeds or raises.
    """
    s = Spond(username, password, "")
    try:
        groups = await s.get_groups()
    except Exception as e:
        logger.error("Failed to fetch groups: %s", e)
        return None
    finally:
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