"""Add sl_price, tp_price, model_id, interval, cooldown_secs to position for OCO re-attach.

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("position") as batch:
        conn = op.get_bind()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("position")}
        if "sl_price" not in cols:
            batch.add_column(sa.Column("sl_price", sa.Float(), nullable=True))
        if "tp_price" not in cols:
            batch.add_column(sa.Column("tp_price", sa.Float(), nullable=True))
        if "model_id" not in cols:
            batch.add_column(sa.Column("model_id", sa.String(), nullable=True))
        if "interval" not in cols:
            batch.add_column(sa.Column("interval", sa.String(), nullable=True))
        if "cooldown_secs" not in cols:
            batch.add_column(sa.Column("cooldown_secs", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("position") as batch:
        batch.drop_column("cooldown_secs")
        batch.drop_column("interval")
        batch.drop_column("model_id")
        batch.drop_column("tp_price")
        batch.drop_column("sl_price")
