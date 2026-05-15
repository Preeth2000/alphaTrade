"""Add stop_order_id and limit_order_id to position table for OCO re-attach on restart.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("position", sa.Column("stop_order_id", sa.String(), nullable=True))
    op.add_column("position", sa.Column("limit_order_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("position", "stop_order_id")
    op.drop_column("position", "limit_order_id")
