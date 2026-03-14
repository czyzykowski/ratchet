"""rename_deploy_to_merge

Revision ID: e9f0a1b2c3d4
Revises: b2c3d4e5f6a7
Create Date: 2026-03-13 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename deploy->merge status strings in event payloads and update trigger."""
    # Update to_status: ready_for_deployment -> ready_for_merge
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{to_status}', '"ready_for_merge"')
        WHERE payload->>'to_status' = 'ready_for_deployment'
    """)

    # Update to_status: deployed -> merged
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{to_status}', '"merged"')
        WHERE payload->>'to_status' = 'deployed'
    """)

    # Update from_status: ready_for_deployment -> ready_for_merge
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{from_status}', '"ready_for_merge"')
        WHERE payload->>'from_status' = 'ready_for_deployment'
    """)

    # Update from_status: deployed -> merged
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{from_status}', '"merged"')
        WHERE payload->>'from_status' = 'deployed'
    """)

    # Update status field: ready_for_deployment -> ready_for_merge
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{status}', '"ready_for_merge"')
        WHERE payload->>'status' = 'ready_for_deployment'
    """)

    # Update status field: deployed -> merged
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{status}', '"merged"')
        WHERE payload->>'status' = 'deployed'
    """)

    # Update event_type: task.deploy_hooks_run -> task.merge_hooks_run
    op.execute("""
        UPDATE events
        SET event_type = 'task.merge_hooks_run'
        WHERE event_type = 'task.deploy_hooks_run'
    """)

    # Replace the notify_compilation_trigger PL/pgSQL function with merged check
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
                  AND NEW.payload->>'to_status' = 'merged' THEN
                PERFORM pg_notify(
                    'ratchet_compilation_trigger',
                    json_build_object('reason', 'task_merged')::text
                );
            END IF;
            RETURN NEW;
        END;
        $$
    """)


def downgrade() -> None:
    """Revert merge->deploy status strings in event payloads and restore trigger."""
    # Revert to_status: ready_for_merge -> ready_for_deployment
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{to_status}', '"ready_for_deployment"')
        WHERE payload->>'to_status' = 'ready_for_merge'
    """)

    # Revert to_status: merged -> deployed
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{to_status}', '"deployed"')
        WHERE payload->>'to_status' = 'merged'
    """)

    # Revert from_status: ready_for_merge -> ready_for_deployment
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{from_status}', '"ready_for_deployment"')
        WHERE payload->>'from_status' = 'ready_for_merge'
    """)

    # Revert from_status: merged -> deployed
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{from_status}', '"deployed"')
        WHERE payload->>'from_status' = 'merged'
    """)

    # Revert status field: ready_for_merge -> ready_for_deployment
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{status}', '"ready_for_deployment"')
        WHERE payload->>'status' = 'ready_for_merge'
    """)

    # Revert status field: merged -> deployed
    op.execute("""
        UPDATE events
        SET payload = jsonb_set(payload, '{status}', '"deployed"')
        WHERE payload->>'status' = 'merged'
    """)

    # Revert event_type: task.merge_hooks_run -> task.deploy_hooks_run
    op.execute("""
        UPDATE events
        SET event_type = 'task.deploy_hooks_run'
        WHERE event_type = 'task.merge_hooks_run'
    """)

    # Restore the notify_compilation_trigger with deployed check
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
