"""fix_current_projects_view

Revision ID: a3f8b2e1d9c0
Revises: 21beadfdc614
Create Date: 2026-03-07 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f8b2e1d9c0"
down_revision: str | None = "21beadfdc614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop and recreate current_projects with repo_url and local_path instead of repo_path."""
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


def downgrade() -> None:
    """Restore original current_projects view with repo_path column."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_projects")
    op.execute("""
        CREATE MATERIALIZED VIEW current_projects AS
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
        CREATE UNIQUE INDEX current_projects_id_idx ON current_projects (id)
    """)
