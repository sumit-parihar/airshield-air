from datetime import datetime

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_ingestion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ingestion_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    health_state_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("device_id", "provider_entry_id", name="uq_reading_device_provider_entry"),
        Index("uq_reading_device_provider_key", "device_id", "provider_entry_key", unique=True),
        Index("ix_readings_pending_alert_evaluation", "alerts_evaluated_at", "timestamp"),
        CheckConstraint("ingestion_mode IN ('live', 'historical_backfill')", name="ck_reading_ingestion_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_entry_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    co2: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm25: Mapped[float | None] = mapped_column(Float, nullable=True)
    voc: Mapped[float | None] = mapped_column(Float, nullable=True)
    voc_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    battery: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="thingspeak")
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    ingestion_mode: Mapped[str] = mapped_column(String(24), default="live", server_default="live", nullable=False)
    alerts_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_evaluation_error: Mapped[str | None] = mapped_column(String(160), nullable=True)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint("parameter IN ('co2', 'pm25', 'voc', 'temperature', 'humidity')", name="ck_alert_rule_parameter"),
        CheckConstraint("operator IN ('gt', 'gte', 'lt', 'lte')", name="ck_alert_rule_operator"),
        CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_alert_rule_severity"),
        CheckConstraint("persistence_seconds >= 0", name="ck_alert_rule_persistence_nonnegative"),
        Index("ix_alert_rules_enabled_parameter", "enabled", "parameter"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    parameter: Mapped[str] = mapped_column(String(20), nullable=False)
    operator: Mapped[str] = mapped_column(String(4), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    persistence_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    recovery_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class RuleEvaluationState(Base):
    __tablename__ = "rule_evaluation_states"
    __table_args__ = (UniqueConstraint("device_id", "rule_id", name="uq_rule_evaluation_device_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    first_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reading_id: Mapped[int] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'acknowledged', 'resolved')", name="ck_alert_status"),
        CheckConstraint("category IN ('environmental', 'device_health')", name="ck_alert_category"),
        CheckConstraint("health_state IS NULL OR health_state IN ('stale', 'offline')", name="ck_alert_health_state"),
        CheckConstraint(
            "(category = 'environmental' AND health_state IS NULL) OR "
            "(category = 'device_health' AND health_state IS NOT NULL AND parameter = 'device_health' "
            "AND rule_id IS NULL AND reading_id IS NULL AND observed_value IS NULL AND threshold IS NULL)",
            name="ck_alert_category_fields",
        ),
        Index("ix_alerts_status_created", "status", "created_at"),
        Index("ix_alerts_device_created", "device_id", "created_at"),
        Index(
            "uq_alert_unresolved_device_rule",
            "device_id",
            "rule_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'acknowledged')"),
            sqlite_where=text("status IN ('active', 'acknowledged')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="RESTRICT"), nullable=True)
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("readings.id", ondelete="RESTRICT"), nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="environmental", server_default="environmental")
    health_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    parameter: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    condition_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)


# Separate partial index avoids changing environmental rule deduplication.
Index(
    "uq_alert_unresolved_device_health",
    Alert.device_id,
    Alert.health_state,
    unique=True,
    postgresql_where=text("category = 'device_health' AND status IN ('active', 'acknowledged')"),
    sqlite_where=text("category = 'device_health' AND status IN ('active', 'acknowledged')"),
)


class NotificationRecord(Base):
    __tablename__ = "notification_records"
    __table_args__ = (
        CheckConstraint("channel IN ('in_app', 'browser', 'email')", name="ck_notification_channel"),
        CheckConstraint("status IN ('recorded', 'pending', 'delivered', 'failed', 'not_configured')", name="ck_notification_status"),
        UniqueConstraint("alert_id", "channel", name="uq_notification_alert_channel"),
        Index("ix_notification_channel_status_created", "channel", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(240), nullable=True)
