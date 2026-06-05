"""Add user_id to model_deployments for per-user model ownership tracking.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_deployments") as batch:
        conn = op.get_bind()
        cols = [c["name"] for c in sa.inspect(conn).get_columns("model_deployments")]
        if "user_id" not in cols:
            batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
    op.create_index("ix_modeldeployments_user_id", "model_deployments", ["user_id"], unique=False,
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_modeldeployments_user_id", "model_deployments")
    with op.batch_alter_table("model_deployments") as batch:
        batch.drop_column("user_id")
