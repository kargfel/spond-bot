"""Initial schema — users and events tables.

Revision ID: 001
Revises:
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("login", sa.String(255), nullable=False),
        sa.Column("encrypted_password", sa.String(), nullable=False),
        sa.Column("encrypted_access_token", sa.String(), nullable=True),
        sa.Column(
            "token_acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("profile_id", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login"),
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spond_event_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("heading", sa.String(500), nullable=True),
        sa.Column("start_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invite_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rsvp_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "user_choice",
            sa.String(10),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "spond_event_id", "user_id", name="uq_event_user"
        ),
    )

    op.create_index(
        "idx_events_invite_status",
        "events",
        ["invite_time", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_events_invite_status", table_name="events")
    op.drop_table("events")
    op.drop_table("users")
