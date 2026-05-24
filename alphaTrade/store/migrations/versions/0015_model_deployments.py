"""Add model_deployments table for deploy lifecycle tracking.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_deployments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_name", sa.String, nullable=False, index=True),
        sa.Column("promoted_at", sa.DateTime, nullable=False),
        sa.Column("activated_at", sa.DateTime, nullable=True),
        sa.Column("failed_at", sa.DateTime, nullable=True),
        sa.Column("failure_msg", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="launching"),
    )


def downgrade() -> None:
    op.drop_table("model_deployments")
