"""Add risk sizing, portfolio, ATR, VIX, and backtest fields to botsettings.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("botsettings")]
    new_cols = [
        ("sizing_mode", sa.String()),
        ("portfolio_mode", sa.String()),
        ("order_stale_window_multiplier", sa.Float()),
        ("order_queue_max_depth", sa.Integer()),
        ("balanced_max_sector_pct", sa.Float()),
        ("unbalanced_max_per_sector", sa.Integer()),
        ("unbalanced_sector_overrides", sa.String()),   # JSON string
        ("atr_risk_pct", sa.Float()),
        ("atr_multiplier", sa.Float()),
        ("vix_base_size_pct", sa.Float()),
        ("vix_scalar", sa.Float()),
        ("vix_max_size_pct", sa.Float()),
        ("backtest_slippage_bps", sa.Integer()),
        ("backtest_commission_per_trade", sa.Float()),
        ("backtest_initial_equity", sa.Float()),
        ("backtest_default_size_pct", sa.Float()),
        ("backtest_sl_pct", sa.Float()),
        ("backtest_tp_pct", sa.Float()),
        ("backtest_schedule_enabled", sa.Boolean()),
        ("backtest_cron", sa.String()),
        ("backtest_lookback_days", sa.Integer()),
        ("backtest_simulate_oco_lag", sa.Boolean()),
        ("backtest_oco_stop_gap_secs", sa.Float()),
        ("backtest_oco_limit_gap_secs", sa.Float()),
    ]
    for col_name, col_type in new_cols:
        if col_name not in cols:
            op.add_column("botsettings", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0014.")
