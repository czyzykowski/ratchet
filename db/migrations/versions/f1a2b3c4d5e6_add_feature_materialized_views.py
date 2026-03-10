"""add_feature_materialized_views

Revision ID: f1a2b3c4d5e6
Revises: 5708622e9db5
Create Date: 2026-03-09 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "5708622e9db5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add current_features and current_high_level_specs materialized views.

    Also updates refresh_all_views() to include both new views.
    """
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
        CREATE MATERIALIZED VIEW current_high_level_specs AS
        WITH added AS (
            SELECT
                (payload->>'hls_id')::UUID      AS id,
                (payload->>'feature_id')::UUID  AS feature_id,
                payload->>'title'               AS title,
                (payload->>'order')::INT        AS "order",
                payload->>'content'             AS content,
                payload->'dependencies'         AS dependencies
            FROM events
            WHERE aggregate_type = 'feature'
              AND event_type = 'high_level_spec.added'
        ),
        compiled AS (
            SELECT DISTINCT ON ((payload->>'hls_id')::UUID)
                (payload->>'hls_id')::UUID   AS id,
                (payload->>'task_id')::UUID  AS task_id
            FROM events
            WHERE aggregate_type = 'feature'
              AND event_type = 'high_level_spec.compiled'
            ORDER BY (payload->>'hls_id')::UUID, sequence DESC
        )
        SELECT
            a.id,
            a.feature_id,
            c.task_id,
            a.title,
            a."order",
            a.content,
            (c.id IS NOT NULL)  AS compiled,
            COALESCE(a.dependencies, '[]'::jsonb) AS dependencies
        FROM added a
        LEFT JOIN compiled c ON c.id = a.id
        WITH NO DATA
    """)
    op.execute(
        "CREATE UNIQUE INDEX current_high_level_specs_id_idx"
        " ON current_high_level_specs (id)"
    )

    # Update refresh_all_views() to include the new materialized views.
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

    op.execute("""
        GRANT SELECT ON current_features, current_high_level_specs TO ratchet_test
    """)


def downgrade() -> None:
    """Remove current_features and current_high_level_specs materialized views.

    Restores refresh_all_views() to exclude the new views.
    """
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_high_level_specs")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_features")

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
        END;
        $$
    """)
