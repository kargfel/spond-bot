"""Add notification_settings and reminder_log.

Revision ID: 009
Revises: 008
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column(
            "frontend_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("frontend_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Every notification is on by default; a login without a row gets the defaults
        sa.Column("answer_sent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("answer_failed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reminder_8h", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reminder_4h", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reminder_1h", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "reminder_log",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("hours", sa.Integer(), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reminder_log")
    op.drop_table("notification_settings")
