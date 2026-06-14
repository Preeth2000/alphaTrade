"""Add index on equitycurve.ts for today_open() query performance.

Revision ID: 0024
Revises: 0023
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_equitycurve_ts", "equitycurve", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_equitycurve_ts", "equitycurve")
