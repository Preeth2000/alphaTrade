from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_override",
        sa.Column("run_name", sa.String(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("broker_ticker", sa.String(), nullable=True),
        sa.Column("size_pct", sa.Float(), nullable=True),
        sa.Column("stop_loss_pct", sa.Float(), nullable=True),
        sa.Column("take_profit_pct", sa.Float(), nullable=True),
        sa.Column("cooldown_bars", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_override")
