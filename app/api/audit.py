"""
/api/v1/admin/audit — The audit trail (admin only).

GET /admin/audit              Filtered, newest first, cursor-paginated
GET /admin/audit/export.csv   The same filters as a CSV download (up to 50,000 rows)

Filters: q (free text), category, outcome, actor_id, since, until.
Reading the trail is not itself logged; exporting it is.
"""
import csv
import io
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, DbDep
from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogEntry, AuditLogPage
from app.services import audit

router = APIRouter(prefix="/admin/audit", tags=["Audit"], dependencies=[AdminDep])

EXPORT_LIMIT = 50_000
CSV_COLUMNS = [
    "occurred_at", "actor_type", "actor_username", "action", "outcome", "target_type", "target_id",
    "target_label", "ip", "user_agent", "method", "path", "status_code", "request_id", "details",
]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(q, *, text, category, outcome, actor_id, since, until):
    if text:
        like = f"%{_escape_like(text.strip())}%"
        q = q.where(or_(*[
            col.ilike(like, escape="\\")
            for col in (AuditLog.action, AuditLog.actor_username, AuditLog.target_label, AuditLog.target_id,
                        AuditLog.ip, AuditLog.path)
        ]))
    if category:
        q = q.where(AuditLog.category == category)
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if since:
        q = q.where(AuditLog.occurred_at >= since)
    if until:
        q = q.where(AuditLog.occurred_at <= until)
    return q


def _encode_cursor(entry: AuditLog) -> str:
    return f"{entry.occurred_at.isoformat()}|{entry.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        stamp, _, row_id = cursor.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(row_id)
    except ValueError:
        raise HTTPException(422, "Invalid cursor.")


@router.get("", response_model=AuditLogPage, summary="Audit trail (admin only)")
async def list_audit(
    db: AsyncSession = DbDep,
    q: str | None = Query(None, max_length=100, description="Matches action, user, target, IP or path"),
    category: str | None = Query(None, max_length=30),
    outcome: str | None = Query(None, pattern="^(success|denied|failed)$"),
    actor_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    query = _filtered(select(AuditLog), text=q, category=category, outcome=outcome,
                      actor_id=actor_id, since=since, until=until)
    if cursor:
        at, row_id = _decode_cursor(cursor)
        query = query.where(or_(AuditLog.occurred_at < at, and_(AuditLog.occurred_at == at, AuditLog.id < row_id)))
    rows = (await db.execute(
        query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit + 1)
    )).scalars().all()
    page, more = rows[:limit], len(rows) > limit
    return AuditLogPage(
        items=[AuditLogEntry.model_validate(r) for r in page],
        next_cursor=_encode_cursor(page[-1]) if more else None,
    )


def _cell(value) -> str:
    """One CSV cell, safe to open in a spreadsheet: formulas are defused."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _value(row: AuditLog, column: str):
    if column == "occurred_at":
        return row.occurred_at.isoformat()
    if column == "details":
        return json.dumps(row.details, ensure_ascii=False) if row.details else ""
    return getattr(row, column)


@router.get("/export.csv", summary="Export the audit trail as CSV (admin only)")
async def export_audit(
    db: AsyncSession = DbDep,
    q: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=30),
    outcome: str | None = Query(None, pattern="^(success|denied|failed)$"),
    actor_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
):
    query = _filtered(select(AuditLog), text=q, category=category, outcome=outcome,
                      actor_id=actor_id, since=since, until=until)
    rows = (await db.execute(
        query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(EXPORT_LIMIT)
    )).scalars().all()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([_cell(_value(r, col)) for col in CSV_COLUMNS])

    audit.record("audit.exported", details={"rows": len(rows), "q": q, "category": category, "outcome": outcome,
                                            "since": since, "until": until})
    return Response(
        content=out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="spondbot-audit.csv"'},
    )
