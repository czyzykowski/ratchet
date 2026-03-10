"""add_compilation_trigger

Revision ID: d2e4f6a8b1c3
Revises: a8f3e2d1c9b0
Create Date: 2026-03-10 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e4f6a8b1c3"
down_revision: str | None = "a8f3e2d1c9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create PL/pgSQL trigger function and attach to events table for compilation triggers."""
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_compilation_trigger()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.event_type = 'high_level_spec.added' THEN
                PERFORM pg_notify(
                    'ratchet_compilation_trigger',
                    json_build_object('reason', 'hls_added')::text
                );
            ELSIF NEW.event_type = 'task.status_changed'
                  AND NEW.payload->>'to_status' = 'deployed' THEN
                PERFORM pg_notify(
                    'ratchet_compilation_trigger',
                    json_build_object('reason', 'task_deployed')::text
                );
            END IF;
            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_notify_compilation
        AFTER INSERT ON events
        FOR EACH ROW
        EXECUTE FUNCTION notify_compilation_trigger()
    """)


def downgrade() -> None:
    """Drop trigger and trigger function."""
    op.execute("DROP TRIGGER IF EXISTS trg_notify_compilation ON events")
    op.execute("DROP FUNCTION IF EXISTS notify_compilation_trigger()")
