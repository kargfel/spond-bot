"""
Audit trail — who did what, when, from where.

Two ways rows get written:

1. Explicit events. Handlers call `record("event.choice_set", target_type=..., details=...)`
   after the change was committed. The row is staged on the current request and written once
   the response is out, together with the request context (IP, user agent, method, path,
   status code, request id). Background work (the bot itself) uses `await record_system(...)`.
2. A safety net in AuditMiddleware: every write request (POST/PUT/PATCH/DELETE), every
   403/429, every 401 on a write and every 5xx that no handler described gets a generic
   `http.<method>` row, so nothing a person does goes unrecorded.

Reads that succeed are not logged (dashboard polling would drown everything else).

Writing an audit row never breaks a request: failures are logged and swallowed.
Secrets never reach the table: `scrub()` drops password/token/key fields from `details`.
"""
import contextvars
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.config import settings
from app.core.jwt import decode_access_token
from app.database import AsyncSessionLocal
from app.models.audit_log import (
    ACTOR_ANONYMOUS,
    ACTOR_SYSTEM,
    ACTOR_USER,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    AuditLog,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sb_session"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
UNLOGGED_PATHS = {"/api/v1/health"}

_SENSITIVE = re.compile(r"pass|token|secret|key|authorization|cookie|credential", re.IGNORECASE)
_MAX_TEXT = 300
_MAX_ITEMS = 20
_MAX_DEPTH = 3

_UNSET: Any = object()
_current: contextvars.ContextVar["RequestContext | None"] = contextvars.ContextVar("audit_request", default=None)


# ── Scrubbing ─────────────────────────────────────────────────────────────


def scrub(value: Any, depth: int = 0) -> Any:
    """Make `details` safe to store: no secrets, bounded size, JSON-friendly values."""
    if depth >= _MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            # A flag like password_reset=True is not a secret; only actual values are redacted.
            secret = _SENSITIVE.search(str(key)) and not isinstance(item, bool) and item is not None
            out[str(key)[:60]] = "[redacted]" if secret else scrub(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [scrub(item, depth + 1) for item in list(value)[:_MAX_ITEMS]]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, (datetime, uuid.UUID)):
        return str(value)
    text = str(value)
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── Request context ───────────────────────────────────────────────────────


@dataclass
class RequestContext:
    request_id: str
    ip: str | None
    user_agent: str | None
    method: str
    path: str
    session_token: str | None
    pending: list[dict] = field(default_factory=list)
    _actor: Any = _UNSET

    def actor(self) -> dict | None:
        """The signed-in login behind this request, from its session cookie (None when anonymous)."""
        if self._actor is _UNSET:
            self._actor = None
            if self.session_token:
                try:
                    self._actor = actor_from(decode_access_token(self.session_token))
                except HTTPException:
                    pass
        return self._actor


def actor_from(source: Any) -> dict | None:
    """Normalise JWT claims or a FrontendUser into {id, username, is_admin}."""
    if source is None:
        return None
    if isinstance(source, dict):
        raw_id, username, is_admin = source.get("sub") or source.get("id"), source.get("username"), source.get("is_admin")
    else:
        raw_id, username, is_admin = source.id, source.username, source.is_admin
    try:
        actor_id = raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id))
    except (ValueError, TypeError):
        actor_id = None
    return {"id": actor_id, "username": username, "is_admin": bool(is_admin)}


def current_request() -> RequestContext | None:
    return _current.get()


def _row(
    action: str,
    outcome: str,
    actor: dict | None,
    actor_type: str,
    target_type: str | None = None,
    target_id: Any = None,
    target_label: str | None = None,
    details: dict | None = None,
) -> dict:
    return {
        "occurred_at": datetime.now(timezone.utc),
        "actor_type": actor_type,
        "actor_id": actor["id"] if actor else None,
        "actor_username": _clip(actor["username"], 255) if actor else None,
        "actor_is_admin": actor["is_admin"] if actor else None,
        "action": _clip(action, 80),
        "category": _clip(action.split(".", 1)[0], 30),
        "outcome": outcome,
        "target_type": _clip(target_type, 40),
        "target_id": _clip(str(target_id), 64) if target_id is not None else None,
        "target_label": _clip(target_label, 255),
        "details": scrub(details) if details else None,
    }


# ── Recording ─────────────────────────────────────────────────────────────


def record(
    action: str,
    *,
    outcome: str = OUTCOME_SUCCESS,
    target_type: str | None = None,
    target_id: Any = None,
    target_label: str | None = None,
    details: dict | None = None,
    actor: Any = _UNSET,
) -> None:
    """
    Stage an audit event on the current request. Call it after the change was committed.

    The actor defaults to the signed-in login of the request. Pass `actor=` when the
    session does not say it yet (login, invite signup) or explicitly `actor=None` for
    "nobody" (a failed login). Outside a request nothing is recorded: use record_system().
    """
    ctx = _current.get()
    if ctx is None or not settings.audit_enabled:
        return
    who = ctx.actor() if actor is _UNSET else actor_from(actor)
    ctx.pending.append(
        _row(action, outcome, who, ACTOR_USER if who else ACTOR_ANONYMOUS, target_type, target_id, target_label, details)
    )


async def record_system(
    action: str,
    *,
    outcome: str = OUTCOME_SUCCESS,
    target_type: str | None = None,
    target_id: Any = None,
    target_label: str | None = None,
    details: dict | None = None,
) -> None:
    """Record something the bot did on its own (RSVP sent, discovery run). Never raises."""
    if not settings.audit_enabled:
        return
    await write_rows([_row(action, outcome, None, ACTOR_SYSTEM, target_type, target_id, target_label, details)])


def open_session():
    """The session audit rows are written with (a seam for tests)."""
    return AsyncSessionLocal()


async def write_rows(rows: list[dict]) -> None:
    if not rows:
        return
    try:
        async with open_session() as db:
            db.add_all([AuditLog(**row) for row in rows])
            await db.commit()
    except Exception:
        logger.exception("Could not write %d audit row(s): %s", len(rows), [r["action"] for r in rows])


# ── Generic safety net ────────────────────────────────────────────────────


def outcome_for_status(status: int) -> str:
    if status < 400:
        return OUTCOME_SUCCESS
    return OUTCOME_DENIED if status in (401, 403, 429) else OUTCOME_FAILED


def should_log_generic(method: str, status: int, path: str, anonymous: bool) -> bool:
    """Whether an undescribed request deserves a generic row."""
    if path in UNLOGGED_PATHS:
        return False
    if status in (403, 429):
        return True
    if method in WRITE_METHODS:
        # Bots probing unknown URLs are noise, but a signed-in person's 404 is not.
        return not (anonymous and status in (404, 405))
    if status == 401:
        return False  # a signed-out visitor loading the sign-in page: normal, not an event
    return status >= 500


def _finish_request(ctx: RequestContext, status: int | None) -> list[dict]:
    """Complete the staged rows with the request's facts, or build the generic row."""
    status = status or 500
    rows = ctx.pending
    if not rows and should_log_generic(ctx.method, status, ctx.path, ctx.actor() is None):
        who = ctx.actor()
        rows = [_row(f"http.{ctx.method.lower()}", outcome_for_status(status), who, ACTOR_USER if who else ACTOR_ANONYMOUS)]
    for row in rows:
        # A handler recorded success, but the request ended in an error (a commit failed afterwards).
        if status >= 400 and row["outcome"] == OUTCOME_SUCCESS:
            row["outcome"] = outcome_for_status(status)
        row.update(
            ip=_clip(ctx.ip, 45),
            user_agent=_clip(ctx.user_agent, 255),
            method=ctx.method,
            path=_clip(ctx.path, 255),
            status_code=status,
            request_id=ctx.request_id,
        )
    return rows


# ── Middleware ────────────────────────────────────────────────────────────


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _session_token(scope: dict) -> str | None:
    cookie = _header(scope, b"cookie")
    if not cookie:
        return None
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            return value or None
    return None


class AuditMiddleware:
    """Pure ASGI middleware: request context in, staged rows out after the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings.audit_enabled:
            return await self.app(scope, receive, send)

        client = scope.get("client")
        ctx = RequestContext(
            request_id=str(uuid.uuid4()),
            ip=client[0] if client else None,
            user_agent=_header(scope, b"user-agent"),
            method=scope["method"],
            path=scope["path"],
            session_token=_session_token(scope),
        )
        reset = _current.set(ctx)
        status: list[int] = []

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
                message.setdefault("headers", []).append((b"x-request-id", ctx.request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            status.append(500)
            raise
        finally:
            _current.reset(reset)
            try:
                await write_rows(_finish_request(ctx, status[0] if status else None))
            except Exception:
                logger.exception("Audit flush failed.")


# ── Reading & retention ───────────────────────────────────────────────────


async def purge_old(db, retention_days: int, now: datetime | None = None) -> int:
    """Delete rows older than the retention. Returns how many were removed."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    count = (await db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.occurred_at < cutoff))).scalar_one()
    if count:
        await db.execute(delete(AuditLog).where(AuditLog.occurred_at < cutoff))
        await db.commit()
    return count


async def run_purge() -> None:
    """Scheduler entry point (nightly). Never raises."""
    if not settings.audit_enabled:
        return
    try:
        async with open_session() as db:
            deleted = await purge_old(db, settings.audit_retention_days)
        if deleted:
            logger.info("Audit log: removed %d entries older than %d days.", deleted, settings.audit_retention_days)
            await record_system("audit.purged", details={"deleted": deleted, "older_than_days": settings.audit_retention_days})
    except Exception:
        logger.exception("Audit purge failed.")
