"""Add visibility to model_override for public library control.

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_override") as batch:
        conn = op.get_bind()
        cols = [c["name"] for c in sa.inspect(conn).get_columns("model_override")]
        if "visibility" not in cols:
            batch.add_column(sa.Column("visibility", sa.String(), nullable=False, server_default="private"))


def downgrade() -> None:
    with op.batch_alter_table("model_override") as batch:
        batch.drop_column("visibility")
