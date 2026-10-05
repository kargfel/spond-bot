"""
NotificationSetting — which notifications a dashboard login wants.

One row per login and shared by all of that login's devices. A login without a row has
every notification on (the defaults), so nothing has to be created for existing members.
"""
import uuid

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# The kinds a member can switch on and off. These are also the column names.
PREFERENCE_KEYS = ("answer_sent", "answer_failed", "reminder_8h", "reminder_4h", "reminder_1h")


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    frontend_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("frontend_users.id", ondelete="CASCADE"), primary_key=True
    )
    answer_sent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    answer_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_8h: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_4h: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_1h: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def as_dict(self) -> dict[str, bool]:
        return {key: getattr(self, key) for key in PREFERENCE_KEYS}
