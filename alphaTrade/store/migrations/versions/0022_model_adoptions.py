"""Create model_adoptions table for public model adoption.

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_adoptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("source_user_id", sa.String(36), nullable=True),
        sa.Column("artifact_prefix", sa.String(), nullable=True),
        sa.Column("adopted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_modeladoptions_user_id", "model_adoptions", ["user_id"])
    op.create_index("ix_modeladoptions_model_name", "model_adoptions", ["model_name"])


def downgrade() -> None:
    op.drop_index("ix_modeladoptions_model_name", "model_adoptions")
    op.drop_index("ix_modeladoptions_user_id", "model_adoptions")
    op.drop_table("model_adoptions")
