"""Add safe_mode and dangerously_allow_pyramid to model_override.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(model_override)"))]
    for col_name in ("safe_mode", "dangerously_allow_pyramid"):
        if col_name not in cols:
            op.add_column("model_override", sa.Column(col_name, sa.Boolean(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0012.")
