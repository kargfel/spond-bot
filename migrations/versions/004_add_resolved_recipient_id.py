"""Add resolved_recipient_id to events table.

Revision ID: 004
Revises: 003
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("resolved_recipient_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "resolved_recipient_id")
