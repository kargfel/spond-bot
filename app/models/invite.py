"""
Invite — a single-use link that lets a new member create their own dashboard
login and connect their own Spond account, so admins never handle members'
Spond passwords.

Only a SHA-256 hash of the token is stored; the token itself is shown to the
admin once, when the invite is created.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

STATUS_PENDING = "pending"
STATUS_USED = "used"
STATUS_EXPIRED = "expired"


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Free-text label for the admin, e.g. the member's name
    note: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("frontend_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("frontend_users.id", ondelete="SET NULL"), nullable=True
    )

    def status(self, now: datetime | None = None) -> str:
        if self.used_at is not None:
            return STATUS_USED
        now = now or datetime.now(timezone.utc)
        return STATUS_EXPIRED if _aware(self.expires_at) <= now else STATUS_PENDING

    def __repr__(self) -> str:
        return f"<Invite note={self.note!r} status={self.status()}>"
