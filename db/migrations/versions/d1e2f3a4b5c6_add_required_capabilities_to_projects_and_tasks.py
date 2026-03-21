"""add required_capabilities to projects view and task capabilities_updated to tasks view

Revision ID: d1e2f3a4b5c6
Revises: a1b2c3d4e5f8
Create Date: 2026-03-21 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str = "a1b2c3d4e5f8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """
    1. Recreate current_projects to include required_capabilities column from PROJECT_CREATED /
       PROJECT_UPDATED payloads (latest PROJECT_UPDATED wins; falls back to PROJECT_CREATED).
    2. Recreate current_tasks to prefer TASK_CAPABILITIES_UPDATED over TASK_CREATED for
       the required_capabilities column.
    """
    # ── current_projects ──────────────────────────────────────────────────────
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("""
        CREATE MATERIALIZED VIEW current_projects AS
        SELECT
            (e.payload->>'project_id')::uuid AS id,
            e.payload->>'name' AS name,
            e.payload->>'repo_url' AS repo_url,
            e.payload->>'local_path' AS local_path,
            e.payload->>'status' AS status,
            COALESCE(e.payload->>'config_source', 'disk') AS config_source,
            cfg.payload->>'claude_md' AS claude_md,
            cfg.payload->>'intent_md' AS intent_md,
            cfg.payload->>'ratchet_yaml' AS ratchet_yaml,
            COALESCE(
                (SELECT payload->'required_capabilities'
                 FROM events
                 WHERE aggregate_type = 'project'
                   AND event_type = 'project.updated'
                   AND (payload->>'project_id')::uuid = (e.payload->>'project_id')::uuid
                 ORDER BY sequence DESC
                 LIMIT 1),
                e.payload->'required_capabilities',
                '[]'::jsonb
            ) AS required_capabilities,
            e.occurred_at AS created_at,
            e.occurred_at AS updated_at
        FROM events e
        LEFT JOIN LATERAL (
            SELECT payload
            FROM events
            WHERE aggregate_type = 'project'
              AND event_type = 'project.config_updated'
              AND (payload->>'project_id')::uuid = (e.payload->>'project_id')::uuid
            ORDER BY sequence DESC
            LIMIT 1
        ) cfg ON true
        WHERE e.aggregate_type = 'project'
          AND e.event_type = 'project.created'
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)")

    # ── current_tasks ─────────────────────────────────────────────────────────
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
        ),
        merge_sha AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id,
                payload->>'merge_commit_sha'   AS merge_commit_sha
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.status_changed'
              AND payload->>'to_status' = 'deployed'
            ORDER BY aggregate_id, sequence DESC
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
                   AND e2.event_type = 'task.capabilities_updated'
                 ORDER BY e2.sequence DESC
                 LIMIT 1),
                (SELECT payload->'required_capabilities'
                 FROM events e2
                 WHERE e2.aggregate_id = c.aggregate_id
                   AND e2.aggregate_type = 'task'
                   AND e2.event_type = 'task.created'
                 LIMIT 1),
                '[]'::jsonb
            )                                                             AS required_capabilities,
            ms.merge_commit_sha
        FROM created c
        LEFT JOIN latest_status ls  ON ls.aggregate_id = c.aggregate_id
        LEFT JOIN latest_spec lsp   ON lsp.aggregate_id = c.aggregate_id
        LEFT JOIN spec_count sc     ON sc.aggregate_id = c.aggregate_id
        LEFT JOIN merge_sha ms      ON ms.aggregate_id = c.aggregate_id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_tasks_id_idx ON current_tasks (id)")


def downgrade() -> None:
    """Restore current_projects and current_tasks to their a1b2c3d4e5f8 definitions."""
    # ── current_projects (restore to e3f4a5b6c7d8 version without required_capabilities) ──
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("""
        CREATE MATERIALIZED VIEW current_projects AS
        SELECT
            (e.payload->>'project_id')::uuid AS id,
            e.payload->>'name' AS name,
            e.payload->>'repo_url' AS repo_url,
            e.payload->>'local_path' AS local_path,
            e.payload->>'status' AS status,
            COALESCE(e.payload->>'config_source', 'disk') AS config_source,
            cfg.payload->>'claude_md' AS claude_md,
            cfg.payload->>'intent_md' AS intent_md,
            cfg.payload->>'ratchet_yaml' AS ratchet_yaml,
            e.occurred_at AS created_at,
            e.occurred_at AS updated_at
        FROM events e
        LEFT JOIN LATERAL (
            SELECT payload
            FROM events
            WHERE aggregate_type = 'project'
              AND event_type = 'project.config_updated'
              AND (payload->>'project_id')::uuid = (e.payload->>'project_id')::uuid
            ORDER BY sequence DESC
            LIMIT 1
        ) cfg ON true
        WHERE e.aggregate_type = 'project'
          AND e.event_type = 'project.created'
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)")

    # ── current_tasks (restore to c1d2e3f4a5b6 version) ──────────────────────
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
        ),
        merge_sha AS (
            SELECT DISTINCT ON (aggregate_id)
                aggregate_id,
                payload->>'merge_commit_sha'   AS merge_commit_sha
            FROM events
            WHERE aggregate_type = 'task'
              AND event_type = 'task.status_changed'
              AND payload->>'to_status' = 'deployed'
            ORDER BY aggregate_id, sequence DESC
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
            )                                                             AS required_capabilities,
            ms.merge_commit_sha
        FROM created c
        LEFT JOIN latest_status ls  ON ls.aggregate_id = c.aggregate_id
        LEFT JOIN latest_spec lsp   ON lsp.aggregate_id = c.aggregate_id
        LEFT JOIN spec_count sc     ON sc.aggregate_id = c.aggregate_id
        LEFT JOIN merge_sha ms      ON ms.aggregate_id = c.aggregate_id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_tasks_id_idx ON current_tasks (id)")
