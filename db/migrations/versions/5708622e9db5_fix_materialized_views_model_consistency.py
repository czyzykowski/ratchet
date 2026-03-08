"""fix_materialized_views_model_consistency

Revision ID: 5708622e9db5
Revises: b4e9c1f2a7d3
Create Date: 2026-03-08 19:45:55.072839

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5708622e9db5"
down_revision: str | None = "b4e9c1f2a7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fix current_projects.updated_at and add branch_name to current_executions."""

    # --- Fix current_projects ---
    # updated_at was locked to the project.created event's occurred_at.
    # Restructure with CTEs so updated_at reflects MAX(occurred_at) across all
    # project events for that aggregate, matching Project.updated_at semantics.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("""
        CREATE MATERIALIZED VIEW current_projects AS
        WITH created AS (
            SELECT
                aggregate_id::UUID       AS id,
                payload->>'name'         AS name,
                payload->>'repo_url'     AS repo_url,
                payload->>'local_path'   AS local_path,
                occurred_at              AS created_at
            FROM events
            WHERE aggregate_type = 'project'
              AND event_type = 'project.created'
        ),
        latest_status AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id::UUID AS id,
                COALESCE(
                    CASE WHEN event_type = 'project.archived' THEN 'archived' END,
                    'active'
                )                  AS status
            FROM events
            WHERE aggregate_type = 'project'
            ORDER BY aggregate_id, sequence DESC
        ),
        last_updated AS (
            SELECT
                aggregate_id::UUID AS id,
                MAX(occurred_at)   AS updated_at
            FROM events
            WHERE aggregate_type = 'project'
            GROUP BY aggregate_id
        )
        SELECT
            c.id,
            c.name,
            c.repo_url,
            c.local_path,
            COALESCE(ls.status, 'active') AS status,
            c.created_at,
            lu.updated_at
        FROM created c
        LEFT JOIN latest_status ls ON ls.id = c.id
        LEFT JOIN last_updated lu  ON lu.id = c.id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)")

    # --- Fix current_executions ---
    # branch_name was missing from the view. The DISTINCT ON approach selected the
    # latest event (completed/failed), which doesn't carry branch_name. Restructure
    # with two CTEs: one anchored on execution.started for stable fields
    # (branch_name, task_id, spec_id, started_at), one for the latest status event.
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


def downgrade() -> None:
    """Restore previous definitions for current_projects and current_executions."""

    # Restore current_projects to the a3f8b2e1d9c0 definition (repo_url/local_path,
    # updated_at locked to project.created occurred_at).
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("""
        CREATE MATERIALIZED VIEW current_projects AS
        SELECT
            (payload->>'project_id')::uuid AS id,
            payload->>'name'               AS name,
            payload->>'repo_url'           AS repo_url,
            payload->>'local_path'         AS local_path,
            payload->>'status'             AS status,
            occurred_at                    AS created_at,
            occurred_at                    AS updated_at
        FROM events
        WHERE aggregate_type = 'project'
          AND event_type = 'project.created'
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)")

    # Restore current_executions to the c67742c064f5 definition (no branch_name).
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_executions")
    op.execute("""
        CREATE MATERIALIZED VIEW current_executions AS
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
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_executions_id_idx ON current_executions (id)")
