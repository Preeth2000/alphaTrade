"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("run_name", sa.String(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("signal", sa.String(), nullable=False),
        sa.Column("model_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("raw_json", sa.String(), nullable=False, server_default=""),
    )

    op.create_table(
        "order",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("t212_ticker", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("t212_order_id", sa.String(), nullable=False, server_default=""),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("error_msg", sa.String(), nullable=False, server_default=""),
        sa.Column("client_order_id", sa.String(), nullable=False, server_default="", index=True),
    )

    op.create_table(
        "position",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("t212_ticker", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("avg_entry", sa.Float(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("last_signal_ts", sa.DateTime(), nullable=True),
        sa.Column("cooldown_until_ts", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "equitycurve",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
    )

    op.create_table(
        "instrumentcache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("yf_ticker", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("t212_ticker", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("instrumentcache")
    op.drop_table("equitycurve")
    op.drop_table("position")
    op.drop_table("order")
    op.drop_table("signal")
