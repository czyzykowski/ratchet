"""use_concurrent_refresh

Revision ID: 56a74ff8c9f1
Revises: e4f5a6b7c8d9
Create Date: 2026-03-24 14:49:25.845088

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "56a74ff8c9f1"
down_revision: str = "e4f5a6b7c8d9"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Switch refresh_all_views() to use CONCURRENTLY to avoid deadlocks."""
    op.execute("""
        CREATE OR REPLACE FUNCTION refresh_all_views()
        RETURNS void AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_projects;
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_tasks;
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_specs;
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_executions;
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_features;
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_high_level_specs;
            REFRESH MATERIALIZED VIEW CONCURRENTLY current_chat_sessions;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION refresh_all_views() TO ratchet_test")


def downgrade() -> None:
    """Revert to non-concurrent refresh."""
    op.execute("""
        CREATE OR REPLACE FUNCTION refresh_all_views()
        RETURNS void AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW current_projects;
            REFRESH MATERIALIZED VIEW current_tasks;
            REFRESH MATERIALIZED VIEW current_specs;
            REFRESH MATERIALIZED VIEW current_executions;
            REFRESH MATERIALIZED VIEW current_features;
            REFRESH MATERIALIZED VIEW current_high_level_specs;
            REFRESH MATERIALIZED VIEW current_chat_sessions;
        END;
        $$ LANGUAGE plpgsql;
    """)
