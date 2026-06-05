"""Add user_id to bot_settings for multi-user support.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-04

The existing singleton row (id=1) is backfilled with the bootstrap sentinel UUID
'00000000-0000-0000-0000-000000000001'. New users get their own row keyed by
their alphaKey user_id.

BotSettings.id (integer PK) is kept unchanged to avoid costly PK migration
on an existing table. user_id is a secondary unique key used for user lookup.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_SENTINEL = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("botsettings")]

    if "user_id" not in cols:
        op.add_column(
            "botsettings",
            sa.Column("user_id", sa.String(36), nullable=True),
        )
        # Backfill existing singleton row
        op.execute(
            f"UPDATE botsettings SET user_id = '{_SENTINEL}' WHERE id = 1 AND user_id IS NULL"
        )
        op.create_index("ix_botsettings_user_id", "botsettings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_botsettings_user_id", "botsettings")
    op.drop_column("botsettings", "user_id")
