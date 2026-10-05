"""
/api/v1/invites — Invite links for member self-onboarding.

POST   /invites          Create a single-use invite (admin only). Returns the token once.
GET    /invites          List invites without tokens (admin only)
DELETE /invites/{id}     Revoke an invite (admin only)
POST   /invites/check    Is this token usable? (public, rate-limited)
POST   /invites/accept   Create a login + connect a Spond account + sign in (public, rate-limited)

Tokens travel in the request body, never in a URL path or query string, so they
do not end up in access logs. The join page reads them from the URL fragment.
"""
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, DbDep
from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.core.session import set_session_cookie
from app.models.frontend_user import FrontendUser
from app.models.invite import STATUS_EXPIRED, STATUS_PENDING, STATUS_USED, Invite
from app.schemas.auth import FrontendUserResponse
from app.schemas.invite import (
    InviteAccept,
    InviteCheckRequest,
    InviteCheckResponse,
    InviteCreate,
    InviteCreated,
    InviteResponse,
)
from app.services import audit
from app.services.spond_accounts import connect_spond_account, ensure_login_available

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/invites", tags=["Invites"])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _to_response(invite: Invite, now: datetime) -> dict:
    return {
        "id": invite.id,
        "note": invite.note,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
        "used_at": invite.used_at,
        "status": invite.status(now),
    }


async def _find(db: AsyncSession, token: str) -> Invite | None:
    result = await db.execute(select(Invite).where(Invite.token_hash == _hash(token)))
    return result.scalar_one_or_none()


# ── Admin ─────────────────────────────────────────────────────────────────


@router.post("", response_model=InviteCreated, status_code=status.HTTP_201_CREATED, summary="Create an invite (admin only)")
async def create_invite(payload: InviteCreate, db: AsyncSession = DbDep, current_user: dict = AdminDep):
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    invite = Invite(
        id=uuid.uuid4(),
        token_hash=_hash(token),
        note=payload.note,
        created_by_id=uuid.UUID(current_user["sub"]),
        created_at=now,
        expires_at=now + timedelta(days=payload.days_valid),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    audit.record("invite.created", target_type="invite", target_id=invite.id, target_label=invite.note,
                 details={"days_valid": payload.days_valid, "expires_at": invite.expires_at})
    logger.info("Invite %s created by %r (note=%r).", invite.id, current_user.get("username"), invite.note)
    return {**_to_response(invite, now), "token": token}


@router.get("", response_model=list[InviteResponse], dependencies=[AdminDep], summary="List invites (admin only)")
async def list_invites(db: AsyncSession = DbDep):
    now = datetime.now(timezone.utc)
    result = await db.execute(select(Invite).order_by(Invite.created_at.desc()))
    return [_to_response(i, now) for i in result.scalars().all()]


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[AdminDep], summary="Revoke an invite (admin only)")
async def revoke_invite(invite_id: uuid.UUID, db: AsyncSession = DbDep):
    invite = await db.get(Invite, invite_id)
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")
    note, status_ = invite.note, invite.status()
    await db.delete(invite)
    await db.commit()
    audit.record("invite.revoked", target_type="invite", target_id=invite_id, target_label=note,
                 details={"status_was": status_})


# ── Public ────────────────────────────────────────────────────────────────


@router.post("/check", response_model=InviteCheckResponse, summary="Check whether an invite can be used")
@limiter.limit("20/minute")
async def check_invite(request: Request, payload: InviteCheckRequest, db: AsyncSession = DbDep):
    invite = await _find(db, payload.token)
    if not invite:
        return {"valid": False, "reason": "unknown", "note": None, "expires_at": None}
    state = invite.status()
    if state != STATUS_PENDING:
        return {"valid": False, "reason": state, "note": None, "expires_at": None}
    return {"valid": True, "reason": None, "note": invite.note, "expires_at": invite.expires_at}


def _accept_failed(reason: str, invite: Invite | None, username: str | None = None) -> None:
    audit.record(
        "invite.accept_failed", outcome="denied", actor=None, target_type="invite",
        target_id=invite.id if invite else None, target_label=invite.note if invite else None,
        details={"reason": reason, "username": (username or "")[:64] or None},
    )


_GONE = {
    None: "This invite link is not valid. Ask your admin for a new one.",
    STATUS_USED: "This invite has already been used. Sign in with the login you created, or ask your admin for a new invite.",
    STATUS_EXPIRED: "This invite has expired. Ask your admin for a new one.",
}


@router.post("/accept", response_model=FrontendUserResponse, status_code=status.HTTP_201_CREATED,
             summary="Accept an invite: create a login, connect Spond, sign in")
@limiter.limit("5/minute")
async def accept_invite(request: Request, response: Response, payload: InviteAccept, db: AsyncSession = DbDep):
    # Cheap checks first: nothing is sent to Spond for an unusable invite or a taken name.
    invite = await _find(db, payload.token)
    state = invite.status() if invite else None
    if state != STATUS_PENDING:
        _accept_failed("invite_" + (state or "unknown"), invite)
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE[state])

    taken = await db.execute(select(FrontendUser.id).where(FrontendUser.username == payload.username))
    if taken.scalar_one_or_none():
        _accept_failed("username_taken", invite, payload.username)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is taken. Pick another one.")
    try:
        await ensure_login_available(db, payload.spond_login)
        spond_user = await connect_spond_account(payload.spond_login, payload.spond_password, payload.display_name)
    except HTTPException as exc:
        _accept_failed("spond_rejected" if exc.status_code == 401 else "spond_account_unavailable", invite, payload.username)
        raise

    # Claim the invite atomically so two simultaneous requests cannot both use it.
    now = datetime.now(timezone.utc)
    claimed = await db.execute(
        update(Invite).where(Invite.id == invite.id, Invite.used_at.is_(None)).values(used_at=now)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        _accept_failed("invite_used", invite, payload.username)
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE[STATUS_USED])

    login = FrontendUser(
        id=uuid.uuid4(),
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_admin=False,
        linked_user_id=spond_user.id,
    )
    db.add(spond_user)
    await db.flush()
    db.add(login)
    await db.flush()
    await db.execute(update(Invite).where(Invite.id == invite.id).values(used_by_id=login.id))
    await db.commit()
    await db.refresh(login)

    set_session_cookie(response, login)
    audit.record("invite.accepted", actor=login, target_type="invite", target_id=invite.id, target_label=invite.note,
                 details={"username": login.username, "spond_login": spond_user.login})
    logger.info("Invite %s accepted: login %r linked to Spond account %r.", invite.id, login.username, spond_user.login)
    return login
