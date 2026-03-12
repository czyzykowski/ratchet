"""add_chat_session_materialized_view

Revision ID: a1b2c3d4e5f7
Revises: f2a3b4c5d6e7
Create Date: 2026-03-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: str = "f2a3b4c5d6e7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add current_chat_sessions materialized view with index on context_id."""
    op.execute("""
        CREATE MATERIALIZED VIEW current_chat_sessions AS
        SELECT
            aggregate_id                        AS id,
            payload->>'session_type'            AS session_type,
            (payload->>'context_id')::uuid      AS context_id,
            payload->>'context_type'            AS context_type,
            occurred_at                         AS created_at
        FROM events
        WHERE aggregate_type = 'chat_session'
          AND event_type = 'chat_session.created'
        WITH NO DATA
    """)
    op.execute(
        "CREATE UNIQUE INDEX current_chat_sessions_id_idx"
        " ON current_chat_sessions (id)"
    )
    op.execute(
        "CREATE INDEX current_chat_sessions_context_id_idx"
        " ON current_chat_sessions (context_id)"
    )

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
            REFRESH MATERIALIZED VIEW current_chat_sessions;
        END;
        $$
    """)

    op.execute("""
        GRANT SELECT ON current_chat_sessions TO ratchet_test
    """)


def downgrade() -> None:
    """Remove current_chat_sessions materialized view."""
    op.execute("DROP MATERIALIZED VIEW IF EXISTS current_chat_sessions")

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
