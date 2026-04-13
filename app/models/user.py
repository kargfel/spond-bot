import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.event import Event


class User(Base):
    """
    Stores one Spond account per row.

    Passwords and tokens are stored Fernet-encrypted. The plaintext
    values are never persisted. `profile_id` is fetched from the Spond
    API on registration and is required for submitting RSVPs.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Email or phone number — used as the Spond login identifier
    login: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    encrypted_password: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(String, nullable=True)
    # UTC timestamp of when the current token was acquired
    token_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Spond internal member ID (32-char hex), required for PUT .../responses/{profileId}
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    events: Mapped[list["Event"]] = relationship(
        "Event", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} login={self.login!r} active={self.is_active}>"
