"""add_task_events_sse_trigger

Revision ID: c3d4e5f6a7b8
Revises: e1f2a3b4c5d6
Create Date: 2026-03-10 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str = "e1f2a3b4c5d6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
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
    op.execute("""
        CREATE TRIGGER trg_notify_task_events
        AFTER INSERT ON events
        FOR EACH ROW EXECUTE FUNCTION notify_task_events();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_notify_task_events ON events;")
    op.execute("DROP FUNCTION IF EXISTS notify_task_events();")
