"""Convert JSON text columns to JSONB (Postgres only).

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Drop defaults before conversion to avoid type mismatch
    op.alter_column("signal", "raw_json", server_default=None)
    op.alter_column("pnlsnapshot", "positions_json", server_default=None)
    op.alter_column("modelperformance", "rolling_trades_json", server_default=None)
    op.alter_column("backtestrun", "config_json", server_default=None)
    # unbalanced_sector_overrides has no default, but include for consistency

    # Convert types
    op.alter_column("signal", "raw_json",
                    type_=postgresql.JSONB, postgresql_using="raw_json::jsonb")
    op.alter_column("pnlsnapshot", "positions_json",
                    type_=postgresql.JSONB, postgresql_using="positions_json::jsonb")
    op.alter_column("modelperformance", "rolling_trades_json",
                    type_=postgresql.JSONB, postgresql_using="rolling_trades_json::jsonb")
    op.alter_column("backtestrun", "config_json",
                    type_=postgresql.JSONB, postgresql_using="config_json::jsonb")
    op.alter_column("botsettings", "unbalanced_sector_overrides",
                    type_=postgresql.JSONB,
                    postgresql_using="unbalanced_sector_overrides::jsonb",
                    nullable=True)

    # Restore defaults with correct JSONB types using raw SQL
    # Note: empty string '' is not valid JSON, so we use null for signal.raw_json
    op.execute(text("ALTER TABLE signal ALTER COLUMN raw_json SET DEFAULT 'null'::jsonb"))
    op.execute(text("ALTER TABLE pnlsnapshot ALTER COLUMN positions_json SET DEFAULT '{}'::jsonb"))
    op.execute(text("ALTER TABLE modelperformance ALTER COLUMN rolling_trades_json SET DEFAULT '[]'::jsonb"))
    op.execute(text("ALTER TABLE backtestrun ALTER COLUMN config_json SET DEFAULT '{}'::jsonb"))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import String

    # Drop defaults before conversion back to String
    op.alter_column("signal", "raw_json", server_default=None)
    op.alter_column("pnlsnapshot", "positions_json", server_default=None)
    op.alter_column("modelperformance", "rolling_trades_json", server_default=None)
    op.alter_column("backtestrun", "config_json", server_default=None)

    # Convert types back to String
    op.alter_column("signal", "raw_json", type_=String)
    op.alter_column("pnlsnapshot", "positions_json", type_=String)
    op.alter_column("modelperformance", "rolling_trades_json", type_=String)
    op.alter_column("backtestrun", "config_json", type_=String)
    op.alter_column("botsettings", "unbalanced_sector_overrides", type_=String, nullable=True)

    # Restore defaults with String types using raw SQL
    op.execute(text("ALTER TABLE signal ALTER COLUMN raw_json SET DEFAULT ''::character varying"))
    op.execute(text("ALTER TABLE pnlsnapshot ALTER COLUMN positions_json SET DEFAULT '{}'::character varying"))
    op.execute(text("ALTER TABLE modelperformance ALTER COLUMN rolling_trades_json SET DEFAULT '[]'::character varying"))
    op.execute(text("ALTER TABLE backtestrun ALTER COLUMN config_json SET DEFAULT '{}'::character varying"))
