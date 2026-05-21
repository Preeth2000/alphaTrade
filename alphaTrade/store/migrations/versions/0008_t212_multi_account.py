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
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("botsettings")]

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

    # Migrate existing keys to the correct slot based on old t212_env value
    if "t212_api_key" in cols and "t212_env" in cols:
        bind.execute(sa.text("""
            UPDATE botsettings
            SET t212_invest_api_key = t212_api_key,
                t212_invest_secret_key = t212_secret_key,
                t212_active_account = 'invest'
            WHERE t212_env = 'live' AND t212_api_key != ''
        """))
        bind.execute(sa.text("""
            UPDATE botsettings
            SET t212_demo_api_key = t212_api_key,
                t212_demo_secret_key = t212_secret_key
            WHERE (t212_env != 'live' OR t212_env IS NULL) AND t212_api_key != ''
        """))
    elif "t212_api_key" in cols:
        bind.execute(sa.text("""
            UPDATE botsettings
            SET t212_demo_api_key = t212_api_key,
                t212_demo_secret_key = COALESCE(t212_secret_key, '')
            WHERE t212_api_key != ''
        """))

    # Drop old columns no longer needed (Postgres supports DROP COLUMN directly)
    for old_col in ("t212_api_key", "t212_secret_key", "t212_env"):
        if old_col in cols:
            op.drop_column("botsettings", old_col)


def downgrade() -> None:
    raise NotImplementedError(
        "Migration 0008 cannot be safely downgraded — invest and ISA keys would be lost. "
        "Restore from backup if rollback is required."
    )
