"""Drop alphaTrade_api_key from botsettings — replaced by JWT auth (alphaKey).

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("botsettings") as batch:
        # Guard: only drop if column still exists (idempotent re-run safety)
        conn = op.get_bind()
        cols = [c["name"] for c in sa.inspect(conn).get_columns("botsettings")]
        if "alphaTrade_api_key" in cols:
            batch.drop_column("alphaTrade_api_key")


def downgrade() -> None:
    with op.batch_alter_table("botsettings") as batch:
        batch.add_column(sa.Column("alphaTrade_api_key", sa.String(), nullable=False, server_default=""))
