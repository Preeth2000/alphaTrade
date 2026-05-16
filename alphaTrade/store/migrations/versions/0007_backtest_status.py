from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtestrun",
        sa.Column("status", sa.String(), nullable=False, server_default="done"),
    )


def downgrade() -> None:
    op.drop_column("backtestrun", "status")
