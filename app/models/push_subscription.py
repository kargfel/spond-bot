"""
PushSubscription — one browser/device that receives Web Push notifications.

Belongs to a dashboard login (FrontendUser): a member can subscribe several devices,
and every one of them is notified when SpondBot answers (or fails to answer) an event
for the Spond account linked to that login. The endpoint is unique: it identifies the
browser's push channel, so signing in as someone else on the same device moves it.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    frontend_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("frontend_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The push service URL for this browser (FCM, Mozilla autopush, Apple, WNS)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # Keys the payload is encrypted with (base64url, from PushSubscription.toJSON())
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    # Shown to the member to tell their devices apart; never used for anything else
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PushSubscription user={self.frontend_user_id} endpoint={self.endpoint[:40]!r}>"
