"""
Stateless async Spond API client.

All functions accept an aiohttp.ClientSession and a token as arguments —
no state is stored here. The database (via app/services/auth.py) is the
single source of truth for tokens.

Errors are surfaced as:
  SpondAuthError  — 401 / bad credentials (caller should force token refresh)
  SpondAPIError   — any other non-2xx response
"""
import logging
from datetime import datetime, timezone

import aiohttp

logger = logging.getLogger(__name__)

_API_BASE = "https://api.spond.com/core/v1/"
_DT_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SpondAuthError(Exception):
    """Raised when the Spond API rejects our credentials (HTTP 401)."""


class SpondAPIError(Exception):
    """Raised for non-auth API failures (4xx/5xx other than 401)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _headers(token: str) -> dict:
    return {
        "content-type": "application/json",
        "Authorization": f"Bearer {token}",
        "user-agent": "Spond-iOS/2.7.10 (2233; iPhone; iOS 26.2.1; Scale/3.00)",
        "Accept-Encoding": "deflate, gzip",
        "accept-language": "en",
        "priority": "u=3, i",
    }


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp from Spond into a UTC-aware datetime."""
    if not value:
        return None
    try:
        # Spond uses formats like "2024-11-07T15:00:00.000Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        logger.warning("Could not parse datetime %r", value)
        return None


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

async def login(
    session: aiohttp.ClientSession,
    user_login: str,
    password: str,
) -> tuple[str, datetime]:
    """
    Authenticate with Spond and return (access_token, acquired_at_utc).

    Accepts an email address or a phone number as user_login.
    Raises SpondAuthError if credentials are rejected.
    """
    url = f"{_API_BASE}auth2/login"
    data = (
        {"email": user_login, "password": password}
        if "@" in user_login
        else {"phoneNumber": user_login, "password": password}
    )

    async with session.post(url, json=data) as r:
        result = await r.json()

    # Old API returned loginToken, new API returns accessToken object
    token = result.get("loginToken")
    if not token:
        access_token_obj = result.get("accessToken")
        if isinstance(access_token_obj, dict):
            # The token seems to be base64 encoded by Spond now, but we just pass it as-is
            # unless the API strictly requires decoding. We'll store it directly.
            # However, since Spond sometimes expects the decoded JWT, let's try decoding it.
            # Actually, standard Spond mobile clients decode it. Let's decode if it's base64 encoded JWT.
            raw_token = access_token_obj.get("token", "")
            import base64
            try:
                # If it's a base64 encoded string of a JWT (starts with eyJ)
                decoded = base64.b64decode(raw_token).decode('utf-8')
                if decoded.startswith("ey"):
                    token = decoded
                else:
                    token = raw_token
            except Exception:
                token = raw_token

    if not token:
        raise SpondAuthError(
            f"Login failed for {user_login!r}. Spond response: {result}"
        )

    logger.debug("Login successful for %r", user_login)
    return token, datetime.now(timezone.utc)


async def get_profile_id(session: aiohttp.ClientSession, token: str) -> str:
    """
    Fetch the user's Spond profile ID (32-char hex string).

    This ID is required as the path parameter in RSVP requests:
    PUT /core/v1/sponds/{spondId}/responses/{profileId}
    """
    url = f"{_API_BASE}profile"
    async with session.get(url, headers=_headers(token)) as r:
        if r.status == 401:
            raise SpondAuthError("Token rejected when fetching profile.")
        if not r.ok:
            text = await r.text()
            raise SpondAPIError(f"Profile fetch failed [{r.status}]: {text}")
        data = await r.json()

    profile_id = data.get("id")
    if not profile_id:
        raise SpondAPIError(f"Profile response missing 'id' field: {data}")

    return profile_id


async def resolve_recipient_id(
    session: aiohttp.ClientSession,
    token: str,
    raw_event: dict,
    user_login: str,
    profile_id: str,
) -> str:
    """
    Find the correct group member ID to use as the RSVP recipient for this event.

    Spond's RSVP endpoint requires the per-group 'member ID', not the global
    'profile ID'. We find it by:
      1. Getting the group ID from the event's recipients.
      2. Fetching all groups (GET /groups includes profile emails/phones).
      3. Filtering to the event's group, then matching member by login email/phone.

    Uses GET /groups (not GET /groups/{id}) because the all-groups response is
    confirmed to include the profile.email / profile.phoneNumber fields needed
    for the match. Falls back to profile_id for direct invites or if no match.
    """
    group_id = raw_event.get("recipients", {}).get("group", {}).get("id")
    if not group_id:
        # Direct invite — no group context, fall back to global profile ID
        logger.debug("Event has no group recipient — using profile_id for RSVP.")
        return profile_id

    url = f"{_API_BASE}groups"
    async with session.get(url, headers=_headers(token)) as r:
        if not r.ok:
            logger.warning("Failed to fetch groups for recipient resolution — using profile_id.")
            return profile_id
        groups = await r.json()

    # Find the specific group this event belongs to
    event_group = next((g for g in groups if g.get("id") == group_id), None)
    if not event_group:
        logger.warning("Group %s not found in user's groups — using profile_id.", group_id)
        return profile_id

    # Primary: match by profile.id — always present, unambiguous
    for member in event_group.get("members", []):
        if (member.get("profile") or {}).get("id") == profile_id:
            m_id = member.get("id")
            if m_id:
                logger.debug(
                    "Resolved recipient_id=%s by profile.id=%s in group=%s",
                    m_id, profile_id, group_id,
                )
                return m_id

    # Secondary: match by login email/phone (Spond sometimes omits profile.id)
    target = user_login.strip().lower()
    for member in event_group.get("members", []):
        member_email   = (member.get("email") or "").strip().lower()
        profile_email  = (member.get("profile", {}).get("email") or "").strip().lower()
        member_phone   = (member.get("phoneNumber") or "").strip().lower()
        profile_phone  = (member.get("profile", {}).get("phoneNumber") or "").strip().lower()

        if target and target in (member_email, profile_email, member_phone, profile_phone):
            m_id = member.get("id")
            if m_id:
                logger.debug(
                    "Resolved recipient_id=%s for login=%r in group=%s",
                    m_id, user_login, group_id,
                )
                return m_id

    logger.warning(
        "No member match in group %s for profile_id=%s login=%r — falling back to profile_id.",
        group_id, profile_id, user_login,
    )
    return profile_id



async def get_upcoming_events(
    session: aiohttp.ClientSession,
    token: str,
    include_declined: bool = True,
    min_end_ts: datetime | None = None,
) -> list[dict]:
    """
    Return upcoming events for the authenticated user.

    Each event dict has at minimum: id, heading.
    """
    url = f"{_API_BASE}sponds/upcoming"
    params: dict = {}
    if include_declined:
        params["includeDeclined"] = "true"
    if min_end_ts:
        params["minEndTimestamp"] = min_end_ts.strftime(_DT_FORMAT)

    async with session.get(url, headers=_headers(token), params=params) as r:
        if r.status == 401:
            raise SpondAuthError("Token rejected on upcoming-events fetch.")
        if not r.ok:
            text = await r.text()
            raise SpondAPIError(f"Upcoming events failed [{r.status}]: {text}")
        return await r.json()


async def get_bulk_events(
    session: aiohttp.ClientSession,
    token: str,
    ids: list[str],
) -> list[dict]:
    """
    Fetch full event details for a list of Spond event IDs.

    Returns dicts that include `inviteTime`, `startTimestamp`, `rsvpDate`,
    and other per-event metadata.
    """
    if not ids:
        return []

    url = f"{_API_BASE}sponds/getBulk"
    params = {"ids": ",".join(ids)}

    async with session.get(url, headers=_headers(token), params=params) as r:
        if r.status == 401:
            raise SpondAuthError("Token rejected on getBulk fetch.")
        if not r.ok:
            text = await r.text()
            raise SpondAPIError(f"getBulk failed [{r.status}]: {text}")
        return await r.json()


async def rsvp(
    session: aiohttp.ClientSession,
    token: str,
    spond_event_id: str,
    profile_id: str,
    accepted: bool,
) -> None:
    """
    Submit an RSVP for the given event.

    PUT /core/v1/sponds/{spondId}/responses/{profileId}
    body: {"accepted": true/false}

    Raises SpondAuthError on 401, SpondAPIError on other failures.
    """
    url = f"{_API_BASE}sponds/{spond_event_id}/responses/{profile_id}"
    payload = {"accepted": accepted}
    action = "ACCEPT" if accepted else "DECLINE"
    logger.debug("RSVP %s → event=%s profile=%s", action, spond_event_id, profile_id)

    async with session.put(url, headers=_headers(token), json=payload) as r:
        if r.status == 401:
            raise SpondAuthError(
                f"Token rejected on RSVP for event {spond_event_id}."
            )
        if r.status not in (200, 204):
            text = await r.text()
            raise SpondAPIError(f"RSVP failed [{r.status}]: {text}")


def parse_event_timestamps(raw: dict) -> dict:
    """
    Extract and parse all relevant timestamp fields from a raw Spond event dict.

    Returns a dict with keys: start_timestamp, invite_time, rsvp_date.
    All values are UTC-aware datetimes or None.
    """
    return {
        "start_timestamp": _parse_dt(raw.get("startTimestamp")),
        "invite_time": _parse_dt(raw.get("inviteTime")),
        "rsvp_date": _parse_dt(raw.get("rsvpDate")),
    }
