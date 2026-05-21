"""Add retirement and backtest schedule fields to model_override.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("model_override")]
    new_cols = [
        ("retirement_enabled", sa.Boolean()),
        ("retirement_lookback_trades", sa.Integer()),
        ("retirement_min_win_rate", sa.Float()),
        ("retirement_min_rolling_pnl", sa.Float()),
        ("retirement_min_trades_before_evaluation", sa.Integer()),
        ("retirement_min_evaluation_period", sa.String()),
        ("backtest_disabled", sa.Boolean()),
        ("backtest_cron", sa.String()),
        ("backtest_lookback_days", sa.Integer()),
    ]
    for col_name, col_type in new_cols:
        if col_name not in cols:
            op.add_column("model_override", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0013.")
