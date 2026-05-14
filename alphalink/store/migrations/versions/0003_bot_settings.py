"""bot settings table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "botsettings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("t212_api_key", sa.String(), nullable=False, server_default=""),
        sa.Column("t212_env", sa.String(), nullable=False, server_default="demo"),
        sa.Column("t212_account_type", sa.String(), nullable=False, server_default="invest"),
        sa.Column("data_provider", sa.String(), nullable=False, server_default="yfinance"),
        sa.Column("polygon_api_key", sa.String(), nullable=False, server_default=""),
        sa.Column("slack_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("slack_webhook_url", sa.String(), nullable=False, server_default=""),
        sa.Column("slack_min_level", sa.String(), nullable=False, server_default="WARNING"),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("email_smtp_host", sa.String(), nullable=False, server_default=""),
        sa.Column("email_smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("email_smtp_user", sa.String(), nullable=False, server_default=""),
        sa.Column("email_smtp_password", sa.String(), nullable=False, server_default=""),
        sa.Column("email_from_addr", sa.String(), nullable=False, server_default=""),
        sa.Column("email_to_addrs", sa.String(), nullable=False, server_default=""),
        sa.Column("email_min_level", sa.String(), nullable=False, server_default="WARNING"),
        sa.Column("size_pct", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("stop_loss_pct", sa.Float(), nullable=False, server_default="0.02"),
        sa.Column("take_profit_pct", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("cooldown_bars", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("extended_hours", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("max_positions", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("daily_loss_halt_pct", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("alphalink_api_key", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("botsettings")
