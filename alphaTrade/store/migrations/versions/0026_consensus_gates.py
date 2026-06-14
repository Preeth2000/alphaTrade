"""Add consensus_min_confidence and consensus_min_margin to bot_settings.

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("botsettings") as batch:
        conn = op.get_bind()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("botsettings")}
        if "consensus_min_confidence" not in cols:
            batch.add_column(sa.Column("consensus_min_confidence", sa.Float(), nullable=True))
        if "consensus_min_margin" not in cols:
            batch.add_column(sa.Column("consensus_min_margin", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("botsettings") as batch:
        batch.drop_column("consensus_min_margin")
        batch.drop_column("consensus_min_confidence")
