"""Add per-model consensus gates to model_override table.

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_override") as batch:
        conn = op.get_bind()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("model_override")}
        if "consensus_min_confidence" not in cols:
            batch.add_column(sa.Column("consensus_min_confidence", sa.Float(), nullable=True))
        if "consensus_min_margin" not in cols:
            batch.add_column(sa.Column("consensus_min_margin", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_override") as batch:
        batch.drop_column("consensus_min_margin")
        batch.drop_column("consensus_min_confidence")
