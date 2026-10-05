"""
AuditLog — append-only record of who did what, when and from where.

One row per audited action. Rows are written by app/services/audit.py and never updated.
The actor's username is copied into the row, and there is no foreign key to the login, so
the trail survives the deletion of the account it describes. Never store secrets in
`details` (the audit service scrubs passwords, tokens and keys before writing).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ACTOR_USER = "user"
ACTOR_ANONYMOUS = "anonymous"
ACTOR_SYSTEM = "system"

OUTCOME_SUCCESS = "success"
OUTCOME_DENIED = "denied"  # refused: not signed in, not allowed, rate limited
OUTCOME_FAILED = "failed"  # allowed but did not work


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_actor_occurred", "actor_id", "occurred_at"),
        Index("ix_audit_log_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # Who: a signed-in login, nobody (not signed in), or the bot itself
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_is_admin: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # What: "auth.login.failed", "event.choice_set", "http.post" (unlisted writes) …
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)

    # On what
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Specifics, e.g. {"from": "manual", "to": "accept"}
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # How and from where (empty for the bot's own actions)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.outcome} actor={self.actor_username!r}>"
