"""Add push_subscriptions table for Web Push notifications.

Revision ID: 006
Revises: 005
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "frontend_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("frontend_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_push_subscriptions_frontend_user_id", "push_subscriptions", ["frontend_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_frontend_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
