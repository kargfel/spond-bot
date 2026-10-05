"""
/api/v1/push — Web Push subscriptions for the signed-in dashboard login.

GET    /push/config        Whether push is available and the VAPID public key
POST   /push/subscribe     Register this browser (PushSubscription.toJSON())
POST   /push/unsubscribe   Forget this browser
POST   /push/test          Send a test notification to the caller's devices

Subscriptions belong to the login, not to the Spond account: every device of a member
is notified when SpondBot answers an event for the Spond account linked to that login.
"""
import logging
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbDep
from app.core.rate_limit import limiter
from app.models.frontend_user import FrontendUser
from app.models.notification_setting import PREFERENCE_KEYS, NotificationSetting
from app.models.push_subscription import PushSubscription
from app.schemas.notification import NotificationPreferences
from app.schemas.push import (
    PushConfigResponse,
    PushSubscribeRequest,
    PushTestResponse,
    PushUnsubscribeRequest,
)
from app.services import audit
from app.services import push as push_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/push", tags=["Push"])


async def _login_id(db: AsyncSession, current_user: dict) -> uuid.UUID:
    """The signed-in login's id; 401 when the account was deleted after the session was issued."""
    try:
        login_id = uuid.UUID(str(current_user.get("sub")))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")
    if await db.get(FrontendUser, login_id) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")
    return login_id


def _require_enabled() -> None:
    if not push_service.push_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Notifications are not set up on this server.")


@router.get("/config", response_model=PushConfigResponse, summary="Push availability and public key")
async def get_config(current_user: dict = CurrentUser):
    return PushConfigResponse(enabled=push_service.push_enabled(), public_key=push_service.public_key())


DEFAULT_PREFERENCES = {key: True for key in PREFERENCE_KEYS}


@router.get("/preferences", response_model=NotificationPreferences, summary="Which notifications this login wants")
async def get_preferences(db: AsyncSession = DbDep, current_user: dict = CurrentUser):
    row = await db.get(NotificationSetting, await _login_id(db, current_user))
    return row.as_dict() if row else DEFAULT_PREFERENCES


@router.put("/preferences", response_model=NotificationPreferences, summary="Choose which notifications this login wants")
async def put_preferences(payload: NotificationPreferences, db: AsyncSession = DbDep, current_user: dict = CurrentUser):
    """Shared by all devices of the login. Every field is required, so nothing is reset by accident."""
    login_id = await _login_id(db, current_user)
    row = await db.get(NotificationSetting, login_id)
    before = row.as_dict() if row else dict(DEFAULT_PREFERENCES)
    after = payload.model_dump()
    if row is None:
        row = NotificationSetting(frontend_user_id=login_id)
        db.add(row)
    for key, value in after.items():
        setattr(row, key, value)
    await db.commit()
    changes = {key: {"from": before[key], "to": after[key]} for key in PREFERENCE_KEYS if before[key] != after[key]}
    audit.record("push.preferences_changed", target_type="login", target_id=login_id, details=changes)
    return after


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT, summary="Register this device")
async def subscribe(
    payload: PushSubscribeRequest,
    request: Request,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    _require_enabled()
    login_id = await _login_id(db, current_user)
    user_agent = (request.headers.get("user-agent") or "")[:255] or None

    existing = (
        await db.execute(select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint))
    ).scalar_one_or_none()
    if existing:
        # Same browser, possibly a different login or rotated keys: it now belongs to the caller.
        existing.frontend_user_id = login_id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.user_agent = user_agent
    else:
        db.add(
            PushSubscription(
                frontend_user_id=login_id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
                user_agent=user_agent,
            )
        )
    await db.commit()

    # Keep the table bounded: a login keeps its newest devices only.
    mine = (
        await db.execute(
            select(PushSubscription.id)
            .where(PushSubscription.frontend_user_id == login_id)
            .order_by(PushSubscription.created_at.desc())
        )
    ).scalars().all()
    if len(mine) > push_service.MAX_DEVICES_PER_LOGIN:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(mine[push_service.MAX_DEVICES_PER_LOGIN:])))
        await db.commit()
    audit.record("push.subscribed", target_type="device", target_label=urlparse(payload.endpoint).hostname,
                 details={"devices": min(len(mine), push_service.MAX_DEVICES_PER_LOGIN)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT, summary="Forget this device")
async def unsubscribe(
    payload: PushUnsubscribeRequest,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    login_id = await _login_id(db, current_user)
    # Scoped to the caller: nobody can remove another member's device.
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.frontend_user_id == login_id,
        )
    )
    await db.commit()
    audit.record("push.unsubscribed", target_type="device", target_label=urlparse(payload.endpoint).hostname)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=PushTestResponse, summary="Send a test notification")
@limiter.limit("5/minute")
async def send_test(
    request: Request,
    db: AsyncSession = DbDep,
    current_user: dict = CurrentUser,
):
    _require_enabled()
    login_id = await _login_id(db, current_user)
    devices, delivered = await push_service.send_to_login(db, login_id, push_service.build_test_payload())
    if devices == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No device is subscribed. Turn notifications on first.")
    audit.record("push.test_sent", outcome="success" if delivered else "failed",
                 details={"devices": devices, "delivered": delivered})
    return PushTestResponse(devices=devices, delivered=delivered)
