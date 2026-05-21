"""Add first_trade_at to modelperformance and retirement fields to botsettings.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Add first_trade_at to modelperformance
    mp_cols = [c["name"] for c in sa.inspect(bind).get_columns("modelperformance")]
    if "first_trade_at" not in mp_cols:
        op.add_column("modelperformance", sa.Column("first_trade_at", sa.DateTime(), nullable=True))

    # Add retirement fields to botsettings
    bs_cols = [c["name"] for c in sa.inspect(bind).get_columns("botsettings")]
    new_cols = [
        ("retirement_enabled", sa.Boolean(), None),
        ("retirement_lookback_trades", sa.Integer(), None),
        ("retirement_min_win_rate", sa.Float(), None),
        ("retirement_min_rolling_pnl", sa.Float(), None),
        ("retirement_min_trades_before_evaluation", sa.Integer(), None),
        ("retirement_min_evaluation_period", sa.String(), None),
    ]
    for col_name, col_type, _ in new_cols:
        if col_name not in bs_cols:
            op.add_column("botsettings", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0009.")
