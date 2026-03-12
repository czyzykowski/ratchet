"""add_required_capabilities_to_tasks

Revision ID: f2a3b4c5d6e7
Revises: e3f4a5b6c7d8
Create Date: 2026-03-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str = "e3f4a5b6c7d8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Drop and recreate current_tasks with required_capabilities JSONB column."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_tasks")
    op.execute("""
        CREATE MATERIALIZED VIEW current_tasks AS
        WITH created AS (
            SELECT
                aggregate_id,
                (payload->>'project_id')::UUID AS project_id,
                payload->>'title'              AS title,
                occurred_at                    AS created_at
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.created'
        ),
        latest_status AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id,
                payload->>'status'             AS status,
                occurred_at                    AS updated_at
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.status_changed'
            ORDER BY aggregate_id, sequence DESC
        ),
        latest_spec AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id,
                (payload->>'spec_id')::UUID    AS current_spec_id
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.spec_assigned'
            ORDER BY aggregate_id, sequence DESC
        ),
        spec_count AS (
            SELECT aggregate_id, COUNT(*) AS refinement_count
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.spec_assigned'
            GROUP BY aggregate_id
        )
        SELECT
            c.aggregate_id::UUID                                          AS id,
            c.project_id,
            c.title,
            COALESCE(ls.status, 'ready_for_spec')                        AS status,
            lsp.current_spec_id,
            COALESCE(sc.refinement_count, 0)::INTEGER                    AS refinement_count,
            c.created_at,
            COALESCE(ls.updated_at, c.created_at)                        AS updated_at,
            COALESCE(
                (SELECT payload->'required_capabilities'
                 FROM events e2
                 WHERE e2.aggregate_id = c.aggregate_id
                   AND e2.aggregate_type = 'task'
                   AND e2.event_type = 'task.created'
                 LIMIT 1),
                '[]'::jsonb
            )                                                             AS required_capabilities
        FROM created c
        LEFT JOIN latest_status ls  ON ls.aggregate_id = c.aggregate_id
        LEFT JOIN latest_spec lsp   ON lsp.aggregate_id = c.aggregate_id
        LEFT JOIN spec_count sc     ON sc.aggregate_id = c.aggregate_id
        WITH NO DATA
    """)
    op.execute("""
        CREATE UNIQUE INDEX current_tasks_id_idx ON current_tasks (id)
    """)


def downgrade() -> None:
    """Restore current_tasks without the required_capabilities column."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_tasks")
    op.execute("""
        CREATE MATERIALIZED VIEW current_tasks AS
        WITH created AS (
            SELECT
                aggregate_id,
                (payload->>'project_id')::UUID AS project_id,
                payload->>'title'              AS title,
                occurred_at                    AS created_at
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.created'
        ),
        latest_status AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id,
                payload->>'status'             AS status,
                occurred_at                    AS updated_at
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.status_changed'
            ORDER BY aggregate_id, sequence DESC
        ),
        latest_spec AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id,
                (payload->>'spec_id')::UUID    AS current_spec_id
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.spec_assigned'
            ORDER BY aggregate_id, sequence DESC
        ),
        spec_count AS (
            SELECT aggregate_id, COUNT(*) AS refinement_count
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.spec_assigned'
            GROUP BY aggregate_id
        )
        SELECT
            c.aggregate_id::UUID                              AS id,
            c.project_id,
            c.title,
            COALESCE(ls.status, 'ready_for_spec')            AS status,
            lsp.current_spec_id,
            COALESCE(sc.refinement_count, 0)::INTEGER        AS refinement_count,
            c.created_at,
            COALESCE(ls.updated_at, c.created_at)            AS updated_at
        FROM created c
        LEFT JOIN latest_status ls  ON ls.aggregate_id = c.aggregate_id
        LEFT JOIN latest_spec lsp   ON lsp.aggregate_id = c.aggregate_id
        LEFT JOIN spec_count sc     ON sc.aggregate_id = c.aggregate_id
        WITH NO DATA
    """)
    op.execute("""
        CREATE UNIQUE INDEX current_tasks_id_idx ON current_tasks (id)
    """)
