from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("devices",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("location", sa.String(200)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_entry_id", sa.Integer()),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("co2", sa.Float()), sa.Column("pm25", sa.Float()), sa.Column("voc", sa.Float()),
        sa.Column("voc_unit", sa.String(32)), sa.Column("temperature", sa.Float()),
        sa.Column("humidity", sa.Float()), sa.Column("device_status", sa.String(64)),
        sa.Column("battery", sa.Float()), sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("device_id", "provider_entry_id", name="uq_reading_device_provider_entry"))
    op.create_index("ix_readings_device_id", "readings", ["device_id"])
    op.create_index("ix_readings_timestamp", "readings", ["timestamp"])


def downgrade():
    op.drop_index("ix_readings_timestamp", table_name="readings")
    op.drop_index("ix_readings_device_id", table_name="readings")
    op.drop_table("readings")
    op.drop_table("devices")
