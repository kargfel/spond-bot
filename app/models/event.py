import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User

# Valid values for user_choice column
CHOICE_ACCEPT = "accept"
CHOICE_DECLINE = "decline"
CHOICE_MANUAL = "manual"
VALID_CHOICES = (CHOICE_ACCEPT, CHOICE_DECLINE, CHOICE_MANUAL)

# Valid values for status column
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"


class Event(Base):
    """
    Represents a Spond event as seen by one specific user.

    Each (spond_event_id, user_id) pair is unique — the same group event
    appears as a separate row per user so that individual RSVP decisions
    can differ.

    The executioner queries on (invite_time, status); the composite index
    `idx_events_invite_status` makes these lookups instant even at scale.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("spond_event_id", "user_id", name="uq_event_user"),
        Index("idx_events_invite_status", "invite_time", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    spond_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    heading: Mapped[str | None] = mapped_column(String(500))
    start_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the event invitation opens for RSVP — used as the executor trigger
    invite_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rsvp_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # User's pre-set intention: 'accept', 'decline', or 'manual' (no-op)
    user_choice: Mapped[str] = mapped_column(
        String(10), default=CHOICE_MANUAL, nullable=False
    )
    # Lifecycle state of this RSVP row
    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(String(1000))
    # Cached by the warmup job ~10s before invite_time; avoids API round-trip at fire time.
    resolved_recipient_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="events")

    def __repr__(self) -> str:
        return (
            f"<Event spond_id={self.spond_event_id!r} "
            f"choice={self.user_choice!r} status={self.status!r}>"
        )
