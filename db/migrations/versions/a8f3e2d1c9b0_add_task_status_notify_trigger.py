"""add_task_status_notify_trigger

Revision ID: a8f3e2d1c9b0
Revises: f1a2b3c4d5e6
Create Date: 2026-03-10 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8f3e2d1c9b0"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create PL/pgSQL trigger function and attach to events table."""
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

    op.execute("""
        CREATE TRIGGER trg_notify_task_status
        AFTER INSERT ON events
        FOR EACH ROW
        EXECUTE FUNCTION notify_task_status_changed()
    """)


def downgrade() -> None:
    """Drop trigger and trigger function."""
    op.execute("DROP TRIGGER IF EXISTS trg_notify_task_status ON events")
    op.execute("DROP FUNCTION IF EXISTS notify_task_status_changed()")
