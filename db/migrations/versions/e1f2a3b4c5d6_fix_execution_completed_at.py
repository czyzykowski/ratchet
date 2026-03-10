"""fix_execution_completed_at

Revision ID: e1f2a3b4c5d6
Revises: d2e4f6a8b1c3
Create Date: 2026-03-10 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d2e4f6a8b1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fix current_executions: use occurred_at as completed_at.

    Instead of reading from payload->>'completed_at'.
    """
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_executions")
    op.execute("""
        CREATE MATERIALIZED VIEW current_executions AS
        WITH started AS (
            SELECT
                aggregate_id::UUID                     AS id,
                (payload->>'task_id')::UUID            AS task_id,
                (payload->>'spec_id')::UUID            AS spec_id,
                payload->>'branch_name'                AS branch_name,
                occurred_at                            AS started_at
            FROM events
            WHERE aggregate_type = 'execution'
              AND event_type = 'execution.started'
        ),
        latest_status AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id::UUID                     AS id,
                CASE
                    WHEN event_type = 'execution.started'   THEN 'running'
                    WHEN event_type = 'execution.completed' THEN 'completed'
                    WHEN event_type = 'execution.failed'    THEN 'failed'
                END                                    AS status,
                payload->>'failure_reason'             AS failure_reason,
                CASE
                    WHEN event_type IN ('execution.completed', 'execution.failed')
                    THEN occurred_at
                END                                    AS completed_at
            FROM events
            WHERE aggregate_type = 'execution'
            ORDER BY aggregate_id, sequence DESC
        )
        SELECT
            s.id,
            s.task_id,
            s.spec_id,
            COALESCE(ls.status, 'running')  AS status,
            ls.failure_reason,
            s.branch_name,
            s.started_at,
            ls.completed_at
        FROM started s
        LEFT JOIN latest_status ls ON ls.id = s.id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_executions_id_idx ON current_executions (id)")


def downgrade() -> None:
    """Restore previous current_executions definition using payload->>'completed_at'."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_executions")
    op.execute("""
        CREATE MATERIALIZED VIEW current_executions AS
        WITH started AS (
            SELECT
                aggregate_id::UUID                     AS id,
                (payload->>'task_id')::UUID            AS task_id,
                (payload->>'spec_id')::UUID            AS spec_id,
                payload->>'branch_name'                AS branch_name,
                occurred_at                            AS started_at
            FROM events
            WHERE aggregate_type = 'execution'
              AND event_type = 'execution.started'
        ),
        latest_status AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id::UUID                     AS id,
                CASE
                    WHEN event_type = 'execution.started'   THEN 'running'
                    WHEN event_type = 'execution.completed' THEN 'completed'
                    WHEN event_type = 'execution.failed'    THEN 'failed'
                END                                    AS status,
                payload->>'failure_reason'             AS failure_reason,
                (payload->>'completed_at')::TIMESTAMPTZ AS completed_at
            FROM events
            WHERE aggregate_type = 'execution'
            ORDER BY aggregate_id, sequence DESC
        )
        SELECT
            s.id,
            s.task_id,
            s.spec_id,
            COALESCE(ls.status, 'running')  AS status,
            ls.failure_reason,
            s.branch_name,
            s.started_at,
            ls.completed_at
        FROM started s
        LEFT JOIN latest_status ls ON ls.id = s.id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_executions_id_idx ON current_executions (id)")
