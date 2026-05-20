"""Add safe_mode and dangerously_allow_pyramid to botsettings.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    bs_cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]
    for col_name in ("safe_mode", "dangerously_allow_pyramid"):
        if col_name not in bs_cols:
            op.add_column("botsettings", sa.Column(col_name, sa.Boolean(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0011.")
