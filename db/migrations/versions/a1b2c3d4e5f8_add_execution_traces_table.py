"""add execution_traces table

Revision ID: a1b2c3d4e5f8
Revises: e9f0a1b2c3d4
Create Date: 2026-03-15 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f8"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS execution_traces (
            execution_id UUID PRIMARY KEY,
            task_id UUID NOT NULL,
            spec_id UUID NOT NULL,
            content TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS execution_traces")
