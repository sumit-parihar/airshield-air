from alembic import op
import sqlalchemy as sa


revision = "0002_ingestion_alerts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("last_successful_ingestion_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("readings", sa.Column("alerts_evaluated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("readings", sa.Column("alert_evaluation_error", sa.String(160), nullable=True))
    op.add_column("readings", sa.Column("provider_entry_key", sa.String(256), nullable=True))
    op.execute(sa.text("UPDATE readings SET provider_entry_key = device_id || ':' || CAST(provider_entry_id AS VARCHAR) WHERE provider_entry_id IS NOT NULL"))
    op.create_index("uq_reading_device_provider_key", "readings", ["device_id", "provider_entry_key"], unique=True)
    op.create_index("ix_readings_pending_alert_evaluation", "readings", ["alerts_evaluated_at", "timestamp"])

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("parameter", sa.String(20), nullable=False),
        sa.Column("operator", sa.String(4), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("persistence_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovery_threshold", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("parameter IN ('co2', 'pm25', 'voc', 'temperature', 'humidity')", name="ck_alert_rule_parameter"),
        sa.CheckConstraint("operator IN ('gt', 'gte', 'lt', 'lte')", name="ck_alert_rule_operator"),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_alert_rule_severity"),
        sa.CheckConstraint("persistence_seconds >= 0", name="ck_alert_rule_persistence_nonnegative"),
    )
    op.create_index("ix_alert_rules_enabled_parameter", "alert_rules", ["enabled", "parameter"])

    op.create_table(
        "rule_evaluation_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(128), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reading_id", sa.Integer(), sa.ForeignKey("readings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("device_id", "rule_id", name="uq_rule_evaluation_device_rule"),
    )
    op.create_index("ix_rule_evaluation_states_device_id", "rule_evaluation_states", ["device_id"])
    op.create_index("ix_rule_evaluation_states_rule_id", "rule_evaluation_states", ["rule_id"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(128), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("alert_rules.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reading_id", sa.Integer(), sa.ForeignKey("readings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("parameter", sa.String(20), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("condition_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.String(120)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.String(32)),
        sa.CheckConstraint("status IN ('active', 'acknowledged', 'resolved')", name="ck_alert_status"),
    )
    op.create_index("ix_alerts_status_created", "alerts", ["status", "created_at"])
    op.create_index("ix_alerts_device_created", "alerts", ["device_id", "created_at"])
    op.create_index(
        "uq_alert_unresolved_device_rule",
        "alerts",
        ["device_id", "rule_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'acknowledged')"),
        sqlite_where=sa.text("status IN ('active', 'acknowledged')"),
    )

    op.create_table(
        "notification_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(240)),
        sa.CheckConstraint("channel IN ('in_app', 'browser', 'email')", name="ck_notification_channel"),
        sa.CheckConstraint("status IN ('recorded', 'pending', 'delivered', 'failed', 'not_configured')", name="ck_notification_status"),
        sa.UniqueConstraint("alert_id", "channel", name="uq_notification_alert_channel"),
    )
    op.create_index("ix_notification_channel_status_created", "notification_records", ["channel", "status", "created_at"])


def downgrade():
    op.drop_index("ix_notification_channel_status_created", table_name="notification_records")
    op.drop_table("notification_records")
    op.drop_index("uq_alert_unresolved_device_rule", table_name="alerts")
    op.drop_index("ix_alerts_device_created", table_name="alerts")
    op.drop_index("ix_alerts_status_created", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_rule_evaluation_states_rule_id", table_name="rule_evaluation_states")
    op.drop_index("ix_rule_evaluation_states_device_id", table_name="rule_evaluation_states")
    op.drop_table("rule_evaluation_states")
    op.drop_index("ix_alert_rules_enabled_parameter", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_readings_pending_alert_evaluation", table_name="readings")
    op.drop_index("uq_reading_device_provider_key", table_name="readings")
    op.drop_column("readings", "provider_entry_key")
    op.drop_column("readings", "alert_evaluation_error")
    op.drop_column("readings", "alerts_evaluated_at")
    op.drop_column("devices", "last_successful_ingestion_at")
