"""initial_schema

Revision ID: c67742c064f5
Revises:
Create Date: 2026-03-06 17:50:09.962281

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c67742c064f5"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create events table and all materialized views."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            aggregate_id    UUID NOT NULL,
            aggregate_type  VARCHAR(50) NOT NULL,
            event_type      VARCHAR(100) NOT NULL,
            payload         JSONB NOT NULL,
            schema_version  INTEGER NOT NULL DEFAULT 1,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            sequence        BIGSERIAL NOT NULL
        )
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS events_sequence_idx ON events (sequence)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS events_aggregate_idx
            ON events (aggregate_id, aggregate_type, sequence)
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS current_projects AS
        SELECT DISTINCT ON (aggregate_id)
            aggregate_id                                 AS id,
            payload->>'name'                             AS name,
            payload->>'repo_path'                        AS repo_path,
            COALESCE(
                CASE WHEN event_type = 'project.archived' THEN 'archived' END,
                'active'
            )                                            AS status,
            MIN(occurred_at) OVER (PARTITION BY aggregate_id) AS created_at,
            occurred_at                                  AS updated_at
        FROM events
        WHERE aggregate_type = 'project'
        ORDER BY aggregate_id, sequence DESC
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS current_projects_id_idx
            ON current_projects (id)
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS current_tasks AS
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
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS current_tasks_id_idx
            ON current_tasks (id)
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS current_specs AS
        SELECT
            aggregate_id::UUID                   AS id,
            (payload->>'task_id')::UUID          AS task_id,
            (payload->>'previous_spec_id')::UUID AS previous_spec_id,
            payload->>'content'                  AS content,
            occurred_at                          AS created_at
        FROM events
        WHERE aggregate_type = 'spec'
          AND event_type = 'spec.created'
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS current_specs_id_idx
            ON current_specs (id)
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS current_executions AS
        SELECT DISTINCT ON (aggregate_id)
            aggregate_id::UUID                      AS id,
            (payload->>'task_id')::UUID             AS task_id,
            (payload->>'spec_id')::UUID             AS spec_id,
            CASE
                WHEN event_type = 'execution.started'   THEN 'running'
                WHEN event_type = 'execution.completed' THEN 'completed'
                WHEN event_type = 'execution.failed'    THEN 'failed'
            END                                      AS status,
            payload->>'failure_reason'               AS failure_reason,
            (payload->>'started_at')::TIMESTAMPTZ    AS started_at,
            (payload->>'completed_at')::TIMESTAMPTZ  AS completed_at
        FROM events
        WHERE aggregate_type = 'execution'
        ORDER BY aggregate_id, sequence DESC
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS current_executions_id_idx
            ON current_executions (id)
    """)


def downgrade() -> None:
    """Drop all materialized views and the events table."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_executions")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_specs")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_tasks")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("DROP TABLE IF EXISTS events")
