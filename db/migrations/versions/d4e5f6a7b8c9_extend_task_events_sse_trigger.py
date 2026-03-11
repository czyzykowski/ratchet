"""extend_task_events_sse_trigger to include baseline_qa events

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-11 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str = "c3d4e5f6a7b8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_task_events()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.event_type IN (
                'task.status_changed',
                'task.baseline_qa_failed',
                'task.baseline_qa_retry'
            ) THEN
                PERFORM pg_notify('task_events', NEW.aggregate_id::text);
            END IF;
            RETURN NEW;
        END;
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_task_events()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.event_type = 'task.status_changed' THEN
                PERFORM pg_notify('task_events', NEW.aggregate_id::text);
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
