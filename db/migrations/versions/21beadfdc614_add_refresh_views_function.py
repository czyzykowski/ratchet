"""add_refresh_views_function

Revision ID: 21beadfdc614
Revises: c67742c064f5
Create Date: 2026-03-06 17:54:21.571658

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "21beadfdc614"
down_revision: str | None = "c67742c064f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a SECURITY DEFINER function so ratchet_test can refresh views."""
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

    op.execute("GRANT EXECUTE ON FUNCTION refresh_all_views() TO ratchet_test")
    op.execute("GRANT INSERT, SELECT ON events TO ratchet_test")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE events_sequence_seq TO ratchet_test")
    op.execute("""
        GRANT SELECT ON current_projects, current_tasks,
                        current_specs, current_executions
        TO ratchet_test
    """)


def downgrade() -> None:
    """Remove refresh helper function and grants."""
    op.execute("DROP FUNCTION IF EXISTS refresh_all_views()")
