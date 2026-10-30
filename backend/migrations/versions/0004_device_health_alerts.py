"""Persist device health transitions and represent them as alerts."""

from alembic import op
import sqlalchemy as sa


revision = "0004_device_health_alerts"
down_revision = "0003_historical_backfill"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("last_ingestion_error_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("health_state", sa.String(16), nullable=True))
    op.add_column("devices", sa.Column("health_state_since", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("category", sa.String(20), nullable=False, server_default="environmental"))
        batch.add_column(sa.Column("health_state", sa.String(16), nullable=True))
        batch.alter_column("rule_id", existing_type=sa.String(36), nullable=True)
        batch.alter_column("reading_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("observed_value", existing_type=sa.Float(), nullable=True)
        batch.alter_column("threshold", existing_type=sa.Float(), nullable=True)
        batch.create_check_constraint("ck_alert_category", "category IN ('environmental', 'device_health')")
        batch.create_check_constraint("ck_alert_health_state", "health_state IS NULL OR health_state IN ('stale', 'offline')")
        batch.create_check_constraint(
            "ck_alert_category_fields",
            "(category = 'environmental' AND health_state IS NULL) OR "
            "(category = 'device_health' AND health_state IS NOT NULL AND parameter = 'device_health' "
            "AND rule_id IS NULL AND reading_id IS NULL AND observed_value IS NULL AND threshold IS NULL)",
        )
    op.create_index(
        "uq_alert_unresolved_device_health", "alerts", ["device_id", "health_state"], unique=True,
        postgresql_where=sa.text("category = 'device_health' AND status IN ('active', 'acknowledged')"),
        sqlite_where=sa.text("category = 'device_health' AND status IN ('active', 'acknowledged')"),
    )


def downgrade():
    op.drop_index("uq_alert_unresolved_device_health", table_name="alerts")
    with op.batch_alter_table("alerts") as batch:
        batch.drop_constraint("ck_alert_health_state", type_="check")
        batch.drop_constraint("ck_alert_category_fields", type_="check")
        batch.drop_constraint("ck_alert_category", type_="check")
        batch.drop_column("health_state")
        batch.drop_column("category")
        batch.alter_column("rule_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("reading_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("observed_value", existing_type=sa.Float(), nullable=False)
        batch.alter_column("threshold", existing_type=sa.Float(), nullable=False)
    op.drop_column("devices", "health_state_since")
    op.drop_column("devices", "health_state")
    op.drop_column("devices", "last_ingestion_error_at")
