"""
Web Push — notifies a member's devices when SpondBot answers an event (or fails to).

Payloads are encrypted for each browser and signed with the server's VAPID key
(pywebpush). Push is optional: without VAPID_PRIVATE_KEY nothing is sent and the
dashboard hides the notification option.

Delivery is best effort and never raises into the RSVP path: a failed notification
must not affect the answer that was just sent.
"""
import asyncio
import base64
import json
import logging
import uuid
from functools import lru_cache
from urllib.parse import urlparse

import aiohttp
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush_async
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.frontend_user import FrontendUser
from app.models.notification_setting import PREFERENCE_KEYS, NotificationSetting
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)

# Real push services only. The endpoint comes from the browser and the server POSTs to it,
# so anything else would let a member aim the server at an arbitrary URL.
ALLOWED_HOST_SUFFIXES = (
    "fcm.googleapis.com",  # Chrome, Edge, Brave, Opera, Samsung Internet
    "push.services.mozilla.com",  # Firefox
    "push.apple.com",  # Safari, iOS home-screen apps
    "notify.windows.com",  # legacy Edge
)

MAX_DEVICES_PER_LOGIN = 20
CHOICE_LABELS = {"accept": "Going", "decline": "Not going", "manual": "Leave to me"}
SEND_TIMEOUT_S = 10
# Notifications about answers are only useful while they are fresh
TTL_SECONDS = 3600

_tasks: set[asyncio.Task] = set()


# ── Configuration ─────────────────────────────────────────────────────────


@lru_cache(maxsize=4)
def _load_vapid(private_key: str) -> tuple[Vapid02, str] | None:
    """Parse the configured key once. Returns (signer, public key as base64url) or None."""
    if not private_key:
        return None
    try:
        vapid = Vapid02.from_string(private_key)
        point = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    except Exception:
        logger.warning("VAPID_PRIVATE_KEY is set but invalid; push notifications are disabled.")
        return None
    return vapid, base64.urlsafe_b64encode(point).rstrip(b"=").decode()


def push_enabled() -> bool:
    return _load_vapid(settings.vapid_private_key) is not None


def public_key() -> str | None:
    loaded = _load_vapid(settings.vapid_private_key)
    return loaded[1] if loaded else None


def _subject() -> str:
    return settings.vapid_subject or f"https://{settings.site_domain}"


def is_allowed_endpoint(endpoint: str) -> bool:
    try:
        url = urlparse(endpoint)
        host = (url.hostname or "").lower()
        port = url.port
    except ValueError:
        return False
    if url.scheme != "https" or url.username or url.password or port not in (None, 443):
        return False
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


# ── Payloads ──────────────────────────────────────────────────────────────


def build_rsvp_payload(event_id: uuid.UUID | str, heading: str | None, choice: str, outcome: str) -> dict:
    """Notification content for one finished RSVP attempt (outcome: success | failed)."""
    name = (heading or "Event").strip()[:120]
    payload = {"tag": f"rsvp-{event_id}", "url": "/dashboard", "outcome": outcome}
    if outcome == "success":
        payload["title"] = f"Answer sent: {CHOICE_LABELS.get(choice, choice)}"
        payload["body"] = name
    else:
        payload["title"] = "SpondBot couldn't answer"
        payload["body"] = f"{name}. Open SpondBot to retry."
    return payload


def build_test_payload() -> dict:
    return {
        "title": "Notifications are on",
        "body": "SpondBot will tell you when it answers an event for you.",
        "tag": "spondbot-test",
        "url": "/dashboard",
        "outcome": "success",
    }


# ── Sending ───────────────────────────────────────────────────────────────

_OK, _GONE, _ERROR = "ok", "gone", "error"


async def _send_one(http: aiohttp.ClientSession, sub: PushSubscription, body: str) -> str:
    loaded = _load_vapid(settings.vapid_private_key)
    if loaded is None:
        return _ERROR
    try:
        await webpush_async(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=body,
            vapid_private_key=loaded[0],
            vapid_claims={"sub": _subject()},
            ttl=TTL_SECONDS,
            headers={"Urgency": "high"},
            timeout=aiohttp.ClientTimeout(total=SEND_TIMEOUT_S),
            aiohttp_session=http,
        )
        return _OK
    except WebPushException as exc:
        # 404/410: the browser dropped the subscription (uninstalled, permission revoked)
        if exc.status_code in (404, 410):
            return _GONE
        logger.warning("Push to %s… failed: %s", sub.endpoint[:50], exc.status_code or exc.message)
    except Exception as exc:
        logger.warning("Push to %s… failed: %s", sub.endpoint[:50], exc)
    return _ERROR


async def _deliver(db: AsyncSession, subs: list[PushSubscription], payload: dict) -> int:
    """Send `payload` to every subscription, drop the dead ones. Returns how many were accepted."""
    if not subs or not push_enabled():
        return 0
    body = json.dumps(payload)
    async with aiohttp.ClientSession() as http:
        results = await asyncio.gather(*[_send_one(http, s, body) for s in subs])
    gone = [s.id for s, r in zip(subs, results) if r == _GONE]
    if gone:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
        await db.commit()
        logger.info("Removed %d expired push subscription(s).", len(gone))
    return sum(1 for r in results if r == _OK)


async def send_to_login(db: AsyncSession, frontend_user_id: uuid.UUID, payload: dict) -> tuple[int, int]:
    """Notify every device of one dashboard login. Returns (devices, delivered)."""
    subs = list(
        (await db.execute(select(PushSubscription).where(PushSubscription.frontend_user_id == frontend_user_id)))
        .scalars()
        .all()
    )
    return len(subs), await _deliver(db, subs, payload)


async def send_to_spond_user(
    db: AsyncSession, spond_user_id: uuid.UUID, payload: dict, kind: str | None = None
) -> tuple[int, int]:
    """
    Notify every device of the dashboard login(s) linked to a Spond account.

    `kind` (one of PREFERENCE_KEYS) restricts this to logins that have that notification
    switched on; a login without saved settings has everything on. The first number returned
    counts the devices that were eligible, so 0 means nobody wanted this notification.
    """
    query = (
        select(PushSubscription)
        .join(FrontendUser, PushSubscription.frontend_user_id == FrontendUser.id)
        .outerjoin(NotificationSetting, NotificationSetting.frontend_user_id == FrontendUser.id)
        .where(FrontendUser.linked_user_id == spond_user_id)
    )
    if kind is not None:
        if kind not in PREFERENCE_KEYS:
            raise ValueError(f"Unknown notification kind {kind!r}")
        query = query.where(func.coalesce(getattr(NotificationSetting, kind), True).is_(True))
    subs = list((await db.execute(query)).scalars().all())
    return len(subs), await _deliver(db, subs, payload)


async def notify_rsvp(spond_user_id: uuid.UUID, event_id: uuid.UUID, heading: str | None, choice: str, outcome: str) -> None:
    """Background entry point for the executioner. Never raises."""
    try:
        async with AsyncSessionLocal() as db:
            kind = "answer_sent" if outcome == "success" else "answer_failed"
            await send_to_spond_user(db, spond_user_id, build_rsvp_payload(event_id, heading, choice, outcome), kind=kind)
    except Exception:
        logger.exception("Push notification for event %s failed.", event_id)


def dispatch_rsvp_notification(
    spond_user_id: uuid.UUID, event_id: uuid.UUID, heading: str | None, choice: str, outcome: str
) -> None:
    """Fire and forget: the RSVP path must not wait for push services."""
    if not push_enabled():
        return
    task = asyncio.create_task(notify_rsvp(spond_user_id, event_id, heading, choice, outcome))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_for_pending() -> None:
    """Let in-flight notifications finish (used by tests and graceful shutdown)."""
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
