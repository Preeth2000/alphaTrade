"""Add error_msg column to backtestrun table.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backtestrun", sa.Column("error_msg", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("backtestrun", "error_msg")
