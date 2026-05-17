"""Add t212_secret_key to botsettings for Basic Auth support.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]
    if "t212_secret_key" not in cols:
        op.add_column(
            "botsettings",
            sa.Column("t212_secret_key", sa.String(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("botsettings", "t212_secret_key")
