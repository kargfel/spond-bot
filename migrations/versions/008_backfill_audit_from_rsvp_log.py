"""Copy the recent answer log into the audit trail.

The admin panel's Log view was folded into the Audit view, so past answers must show up
there too. The rsvp_log table itself stays (statistics and charts read it). Only the last
90 days are copied, matching the default audit retention, and nothing is copied for answers
the audit trail already recorded itself, so running this after audit logging went live
does not create duplicates.

Revision ID: 008
Revises: 007
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO audit_log (
            id, occurred_at, actor_type, action, category, outcome,
            target_type, target_id, target_label, details
        )
        SELECT
            gen_random_uuid(),
            r.fired_at,
            'system',
            CASE WHEN r.outcome = 'failed' THEN 'rsvp.failed' ELSE 'rsvp.sent' END,
            'rsvp',
            CASE WHEN r.outcome = 'failed' THEN 'failed' ELSE 'success' END,
            'event',
            r.event_id::text,
            e.heading,
            json_strip_nulls(json_build_object(
                'choice', r.choice,
                'member', u.display_name,
                'spond_user_id', r.user_id::text,
                'spond_event_id', r.spond_event_id,
                'latency_ms', CASE
                    WHEN r.submitted_at IS NOT NULL AND e.invite_time IS NOT NULL
                    THEN round(EXTRACT(EPOCH FROM (r.submitted_at - e.invite_time)) * 1000)::int
                END,
                'retries', NULLIF(r.retry_count, 0),
                'error', left(r.error_detail, 300),
                'backfilled', true
            ))
        FROM rsvp_log r
        LEFT JOIN events e ON e.id = r.event_id
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.fired_at > now() - interval '90 days'
          AND r.fired_at < COALESCE(
              (SELECT min(occurred_at) FROM audit_log WHERE category = 'rsvp'),
              'infinity'::timestamptz
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM audit_log WHERE category = 'rsvp' AND (details->>'backfilled') = 'true'")
