"""add_abandoned_to_current_features

Revision ID: e4f5a6b7c8d9
Revises: d5e6f7a8b9c0
Create Date: 2026-03-23 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop and recreate current_features with abandoned boolean column.

    abandoned is true if a feature.abandoned event exists for the feature.
    Also updates refresh_all_views() to include current_high_level_specs.
    """
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_features")
    op.execute("""
        CREATE MATERIALIZED VIEW current_features AS
        WITH created AS (
            SELECT
                (payload->>'feature_id')::UUID  AS id,
                (payload->>'project_id')::UUID  AS project_id,
                payload->>'title'               AS title,
                payload->>'description'         AS description,
                occurred_at                     AS created_at
            FROM events
            WHERE aggregate_type = 'feature'
              AND event_type = 'feature.created'
        ),
        last_updated AS (
            SELECT
                aggregate_id::UUID  AS id,
                MAX(occurred_at)    AS updated_at
            FROM events
            WHERE aggregate_type = 'feature'
            GROUP BY aggregate_id
        ),
        abandoned AS (
            SELECT DISTINCT (payload->>'feature_id')::UUID AS id
            FROM events
            WHERE aggregate_type = 'feature'
              AND event_type = 'feature.abandoned'
        )
        SELECT
            c.id,
            c.project_id,
            c.title,
            c.description,
            c.created_at,
            COALESCE(lu.updated_at, c.created_at) AS updated_at,
            (a.id IS NOT NULL) AS abandoned
        FROM created c
        LEFT JOIN last_updated lu ON lu.id = c.id
        LEFT JOIN abandoned a ON a.id = c.id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_features_id_idx ON current_features (id)")

    op.execute("""
        CREATE OR REPLACE FUNCTION refresh_all_views()
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW current_projects;
            REFRESH MATERIALIZED VIEW current_tasks;
            REFRESH MATERIALIZED VIEW current_specs;
            REFRESH MATERIALIZED VIEW current_executions;
            REFRESH MATERIALIZED VIEW current_features;
            REFRESH MATERIALIZED VIEW current_high_level_specs;
        END;
        $$
    """)


def downgrade() -> None:
    """Restore current_features without abandoned column."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_features")
    op.execute("""
        CREATE MATERIALIZED VIEW current_features AS
        WITH created AS (
            SELECT
                (payload->>'feature_id')::UUID  AS id,
                (payload->>'project_id')::UUID  AS project_id,
                payload->>'title'               AS title,
                payload->>'description'         AS description,
                occurred_at                     AS created_at
            FROM events
            WHERE aggregate_type = 'feature'
              AND event_type = 'feature.created'
        ),
        last_updated AS (
            SELECT
                aggregate_id::UUID  AS id,
                MAX(occurred_at)    AS updated_at
            FROM events
            WHERE aggregate_type = 'feature'
            GROUP BY aggregate_id
        )
        SELECT
            c.id,
            c.project_id,
            c.title,
            c.description,
            c.created_at,
            COALESCE(lu.updated_at, c.created_at) AS updated_at
        FROM created c
        LEFT JOIN last_updated lu ON lu.id = c.id
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX current_features_id_idx ON current_features (id)")

    op.execute("""
        CREATE OR REPLACE FUNCTION refresh_all_views()
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW current_projects;
            REFRESH MATERIALIZED VIEW current_tasks;
            REFRESH MATERIALIZED VIEW current_specs;
            REFRESH MATERIALIZED VIEW current_executions;
            REFRESH MATERIALIZED VIEW current_features;
            REFRESH MATERIALIZED VIEW current_high_level_specs;
        END;
        $$
    """)
