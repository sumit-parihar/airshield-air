"""Distinguish live ingestion from explicit historical backfill."""

from alembic import op
import sqlalchemy as sa


revision = "0003_historical_backfill"
down_revision = "0002_ingestion_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "readings",
        sa.Column("ingestion_mode", sa.String(length=24), nullable=False, server_default="live"),
    )
    # SQLite requires a table rebuild for ALTER CONSTRAINT; batch mode also
    # produces the native constraint directly on PostgreSQL.
    with op.batch_alter_table("readings") as batch_op:
        batch_op.create_check_constraint(
            "ck_reading_ingestion_mode",
            "ingestion_mode IN ('live', 'historical_backfill')",
        )


def downgrade():
    with op.batch_alter_table("readings") as batch_op:
        batch_op.drop_constraint("ck_reading_ingestion_mode", type_="check")
    op.drop_column("readings", "ingestion_mode")
