"""Replace t212 single key pair with per-account key pairs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]

    new_cols = [
        ("t212_active_account", "demo"),
        ("t212_demo_api_key", ""),
        ("t212_demo_secret_key", ""),
        ("t212_invest_api_key", ""),
        ("t212_invest_secret_key", ""),
        ("t212_isa_api_key", ""),
        ("t212_isa_secret_key", ""),
    ]
    for col_name, default in new_cols:
        if col_name not in cols:
            op.add_column(
                "botsettings",
                sa.Column(col_name, sa.String(), nullable=False, server_default=default),
            )

    # Migrate existing keys to demo slot
    if "t212_api_key" in cols:
        conn.execute(sa.text(
            "UPDATE botsettings SET t212_demo_api_key = t212_api_key "
            "WHERE t212_api_key != '' AND t212_demo_api_key = ''"
        ))
    if "t212_secret_key" in cols:
        conn.execute(sa.text(
            "UPDATE botsettings SET t212_demo_secret_key = t212_secret_key "
            "WHERE t212_secret_key != '' AND t212_demo_secret_key = ''"
        ))

    # Recreate table without old columns (SQLite DROP COLUMN workaround)
    conn.execute(sa.text("""
        CREATE TABLE botsettings_new AS
        SELECT
            id,
            t212_active_account,
            t212_demo_api_key,
            t212_demo_secret_key,
            t212_invest_api_key,
            t212_invest_secret_key,
            t212_isa_api_key,
            t212_isa_secret_key,
            data_provider,
            polygon_api_key,
            slack_enabled,
            slack_webhook_url,
            slack_min_level,
            email_enabled,
            email_smtp_host,
            email_smtp_port,
            email_smtp_user,
            email_smtp_password,
            email_from_addr,
            email_to_addrs,
            email_min_level,
            size_pct,
            stop_loss_pct,
            take_profit_pct,
            cooldown_bars,
            extended_hours,
            max_positions,
            daily_loss_halt_pct,
            alphaTrade_api_key
        FROM botsettings
    """))
    conn.execute(sa.text("DROP TABLE botsettings"))
    conn.execute(sa.text("ALTER TABLE botsettings_new RENAME TO botsettings"))


def downgrade() -> None:
    raise NotImplementedError(
        "Migration 0008 cannot be safely downgraded — invest and ISA keys would be lost. "
        "Restore from backup if rollback is required."
    )
