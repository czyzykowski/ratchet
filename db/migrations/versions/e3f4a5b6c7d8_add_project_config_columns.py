"""add project config columns to current_projects view

Revision ID: e3f4a5b6c7d8
Revises: d4e5f6a7b8c9
Create Date: 2026-03-11 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str = "d4e5f6a7b8c9"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Drop and recreate current_projects with config_source, claude_md, intent_md, ratchet_yaml."""
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
    op.execute("""
        CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)
    """)


def downgrade() -> None:
    """Restore current_projects without config columns."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("""
        CREATE MATERIALIZED VIEW current_projects AS
        SELECT
            (payload->>'project_id')::uuid AS id,
            payload->>'name' AS name,
            payload->>'repo_url' AS repo_url,
            payload->>'local_path' AS local_path,
            payload->>'status' AS status,
            occurred_at AS created_at,
            occurred_at AS updated_at
        FROM events
        WHERE aggregate_type = 'project'
          AND event_type = 'project.created'
        WITH NO DATA
    """)
    op.execute("""
        CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)
    """)
