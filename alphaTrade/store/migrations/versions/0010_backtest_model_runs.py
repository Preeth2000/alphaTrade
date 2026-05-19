"""Add backtestmodelrun table for per-model run tracking.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtestmodelrun",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("interval", sa.String(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="ran"),
        sa.Column("error_msg", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("backtestmodelrun")
