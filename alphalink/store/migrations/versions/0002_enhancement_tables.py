"""enhancement tables: trade_journal, pnl_snapshot, model_performance, sector_cache, backtest_run, backtest_trade

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tradejournal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False, index=True),
        sa.Column("ticker", sa.String(), nullable=False, index=True),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("entry_time", sa.DateTime(), nullable=False),
        sa.Column("exit_time", sa.DateTime(), nullable=False),
        sa.Column("hold_bars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_reason", sa.String(), nullable=False),
        sa.Column("sl_price", sa.Float(), nullable=True),
        sa.Column("tp_price", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("pnl_pct", sa.Float(), nullable=False),
    )

    op.create_table(
        "pnlsnapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("total_equity", sa.Float(), nullable=False),
        sa.Column("day_pnl", sa.Float(), nullable=False),
        sa.Column("day_pnl_pct", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False),
        sa.Column("positions_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("open_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "modelperformance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rolling_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rolling_trades_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("retired", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "sectorcache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("yf_ticker", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("sector", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "backtestrun",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("start_date", sa.String(), nullable=False),
        sa.Column("end_date", sa.String(), nullable=False),
        sa.Column("config_json", sa.String(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "backtesttrade",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("entry_bar", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_bar", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entry_time", sa.DateTime(), nullable=False),
        sa.Column("exit_time", sa.DateTime(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("sl_price", sa.Float(), nullable=True),
        sa.Column("tp_price", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backtesttrade")
    op.drop_table("backtestrun")
    op.drop_table("sectorcache")
    op.drop_table("modelperformance")
    op.drop_table("pnlsnapshot")
    op.drop_table("tradejournal")
