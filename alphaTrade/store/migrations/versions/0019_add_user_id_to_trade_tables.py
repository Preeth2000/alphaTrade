"""Add user_id to all per-user trade data tables.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-04

All existing rows are backfilled with the bootstrap sentinel UUID so
existing single-tenant data is still accessible via the sentinel user_id.
All columns are nullable for backwards compat; NOT NULL enforcement is Phase 5.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_SENTINEL = "00000000-0000-0000-0000-000000000001"

# (table_name, index_name)
_TABLES = [
    ("signal",           "ix_signal_user_id"),
    ("order",            "ix_order_user_id"),
    ("position",         "ix_position_user_id"),
    ("equitycurve",      "ix_equitycurve_user_id"),
    ("tradejournal",     "ix_tradejournal_user_id"),
    ("pnlsnapshot",      "ix_pnlsnapshot_user_id"),
    ("backtestrun",      "ix_backtestrun_user_id"),
    ("backtesttrade",    "ix_backtesttrade_user_id"),
    ("backtestmodelrun", "ix_backtestmodelrun_user_id"),
    ("modelperformance", "ix_modelperformance_user_id"),
    ("modeldeployment",  "ix_modeldeployment_user_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table, idx in _TABLES:
        if table not in existing_tables:
            continue
        cols = [c["name"] for c in inspector.get_columns(table)]
        if "user_id" not in cols:
            op.add_column(table, sa.Column("user_id", sa.String(36), nullable=True))
            op.execute(f"UPDATE \"{table}\" SET user_id = '{_SENTINEL}' WHERE user_id IS NULL")
            op.create_index(idx, table, ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table, idx in reversed(_TABLES):
        if table not in existing_tables:
            continue
        cols = [c["name"] for c in inspector.get_columns(table)]
        if "user_id" in cols:
            try:
                op.drop_index(idx, table)
            except Exception:
                pass
            op.drop_column(table, "user_id")
