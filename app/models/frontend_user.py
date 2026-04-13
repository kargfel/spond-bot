"""
FrontendUser — accounts for the SpondBot web UI.

These are NOT Spond accounts. They are local credentials that control
access to this dashboard. Each FrontendUser may be linked (optionally)
to one Spond User row via `linked_user_id` — that link determines what
events a non-admin user is allowed to see.

Passwords are stored as bcrypt hashes. Plaintext is never persisted.
"""
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class FrontendUser(Base):
    __tablename__ = "frontend_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Login name shown on the dashboard (e.g. "felix")
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # bcrypt hash of the user's password
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    # True  → can see all users/events, manage everything
    # False → can only see their own linked Spond user's events
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optional FK linking this dashboard account to a Spond user row.
    # None for admin accounts (they don't need a Spond profile).
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    linked_user: Mapped["User | None"] = relationship("User", foreign_keys=[linked_user_id])

    def __repr__(self) -> str:
        return f"<FrontendUser username={self.username!r} admin={self.is_admin}>"
