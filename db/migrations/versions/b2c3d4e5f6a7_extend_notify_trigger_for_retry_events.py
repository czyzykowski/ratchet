"""extend_notify_trigger_for_retry_events

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-03-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend the notify trigger to fire on retry/force events as well."""
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_task_status_changed()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_status text;
        BEGIN
            IF NEW.event_type = 'task.status_changed' THEN
                v_status := NEW.payload->>'to_status';
            ELSIF NEW.event_type IN ('task.baseline_qa_retry', 'task.force_execute') THEN
                v_status := 'ready_for_implementation';
            ELSE
                RETURN NEW;
            END IF;

            PERFORM pg_notify(
                'ratchet_task_status',
                json_build_object(
                    'task_id', NEW.aggregate_id::text,
                    'status',  v_status
                )::text
            );
            RETURN NEW;
        END;
        $$
    """)


def downgrade() -> None:
    """Restore original trigger that only fires on task.status_changed."""
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_task_status_changed()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.event_type = 'task.status_changed' THEN
                PERFORM pg_notify(
                    'ratchet_task_status',
                    json_build_object(
                        'task_id', NEW.aggregate_id::text,
                        'status',  NEW.payload->>'to_status'
                    )::text
                );
            END IF;
            RETURN NEW;
        END;
        $$
    """)
