"""add_events_aggregate_id_and_type_indexes

Revision ID: b4e9c1f2a7d3
Revises: a3f8b2e1d9c0
Create Date: 2026-03-08 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e9c1f2a7d3"
down_revision: str | None = "a3f8b2e1d9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add single-column indexes on events.aggregate_id and events.aggregate_type."""
    op.execute("""CREATE INDEX IF NOT EXISTS events_aggregate_id_idx ON events (aggregate_id)""")
    op.execute(
        """CREATE INDEX IF NOT EXISTS events_aggregate_type_idx ON events (aggregate_type)"""
    )


def downgrade() -> None:
    """Drop single-column indexes on events.aggregate_id and events.aggregate_type."""
    op.execute("""DROP INDEX IF EXISTS events_aggregate_id_idx""")
    op.execute("""DROP INDEX IF EXISTS events_aggregate_type_idx""")
