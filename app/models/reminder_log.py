"""
ReminderLog — which "registration opens soon" reminders were already sent for an event.

The primary key (event, hours) makes sending at most once atomic: a reminder is claimed by
inserting its row, and a second attempt (a restart, an overlapping run) fails on the key.
Rows disappear with their event.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReminderLog(Base):
    __tablename__ = "reminder_log"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    # The threshold that was reached: 8, 4 or 1 hours before registration opens
    hours: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
