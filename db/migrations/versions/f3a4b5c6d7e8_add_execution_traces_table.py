"""add_execution_traces_table

Revision ID: f3a4b5c6d7e8
Revises: e9f0a1b2c3d4
Create Date: 2026-03-15 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str = "e9f0a1b2c3d4"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create execution_traces table."""
    op.execute("""
        CREATE TABLE execution_traces (
            execution_id  UUID        PRIMARY KEY,
            task_id       UUID        NOT NULL,
            spec_id       UUID        NOT NULL,
            content       TEXT        NOT NULL,
            started_at    TIMESTAMPTZ NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX execution_traces_task_id_idx ON execution_traces (task_id)
    """)


def downgrade() -> None:
    """Drop execution_traces table."""
    op.execute("DROP TABLE IF EXISTS execution_traces")
