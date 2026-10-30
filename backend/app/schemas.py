from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Parameter = Literal["co2", "pm25", "voc", "temperature", "humidity"]
AlertParameter = Literal["co2", "pm25", "voc", "temperature", "humidity", "device_health"]
Operator = Literal["gt", "gte", "lt", "lte"]
Severity = Literal["info", "warning", "critical"]
AlertStatus = Literal["active", "acknowledged", "resolved"]
IngestionMode = Literal["live", "historical_backfill"]


class AirQualityReading(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    provider_entry_id: int | None = None
    device_id: str
    timestamp: datetime
    co2: float | None = None
    pm25: float | None = None
    voc: float | None = None
    voc_unit: str | None = None
    temperature: float | None = None
    humidity: float | None = None
    device_status: str | None = None
    battery: float | None = None
    source: str = "thingspeak"
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    ingestion_mode: IngestionMode | None = None


class LatestReadingsResponse(BaseModel):
    readings: list[AirQualityReading]
    source: str
    fetched_at: datetime
    stale: bool


class ReadingPage(BaseModel):
    items: list[AirQualityReading]
    total: int
    offset: int
    limit: int


class MetricStatistics(BaseModel):
    count: int
    minimum: float | None
    maximum: float | None
    average: float | None
    unit: str | None


class RuleExceedanceDuration(BaseModel):
    rule_id: str
    parameter: Parameter
    operator: Operator
    threshold: float
    unit: str
    ingestion_mode: IngestionMode
    observed_seconds: int = Field(ge=0)
    observed_intervals: int = Field(ge=0)


class AnalyticsSummary(BaseModel):
    start_at: datetime
    end_at: datetime
    device_id: str | None
    reading_count: int
    ingestion_mode_counts: dict[IngestionMode, int]
    metrics: dict[str, MetricStatistics]
    alert_counts_by_severity: dict[str, int]
    alert_counts_by_parameter: dict[str, int]
    exceedance_durations: list[RuleExceedanceDuration] = Field(default_factory=list)


class TrendPoint(BaseModel):
    timestamp: datetime
    value: float
    sample_count: int


class TrendResponse(BaseModel):
    parameter: Parameter
    unit: str | None
    interval_seconds: int
    samples_in_range: int
    ingestion_modes: list[IngestionMode]
    points: list[TrendPoint]


class HealthResponse(BaseModel):
    status: str
    database: str


PARAMETER_UNITS = {
    "co2": "ppm",
    "pm25": "µg/m³",
    "temperature": "°C",
    "humidity": "%RH",
}


class AlertRuleFields(BaseModel):
    parameter: Parameter
    operator: Operator
    threshold: FiniteFloat
    unit: str = Field(min_length=1, max_length=32)
    severity: Severity
    enabled: bool = True
    persistence_seconds: int = Field(default=0, ge=0, le=604800)
    recovery_threshold: FiniteFloat | None = None
    message: str = Field(min_length=1, max_length=500)
    recommended_action: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_parameter_unit_and_recovery(self):
        expected = PARAMETER_UNITS.get(self.parameter)
        if expected and self.unit != expected:
            raise ValueError(f"{self.parameter} unit must be {expected}")
        if self.parameter == "voc" and not self.unit.strip():
            raise ValueError("VOC unit must be explicitly configured")
        if self.recovery_threshold is not None:
            high_rule = self.operator in ("gt", "gte")
            if high_rule and self.recovery_threshold >= self.threshold:
                raise ValueError("A high-threshold rule recovery value must be below its trigger threshold")
            if not high_rule and self.recovery_threshold <= self.threshold:
                raise ValueError("A low-threshold rule recovery value must be above its trigger threshold")
        return self


class AlertRuleCreate(AlertRuleFields):
    pass


class AlertRuleUpdate(BaseModel):
    parameter: Parameter | None = None
    operator: Operator | None = None
    threshold: FiniteFloat | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    severity: Severity | None = None
    enabled: bool | None = None
    persistence_seconds: int | None = Field(default=None, ge=0, le=604800)
    recovery_threshold: FiniteFloat | None = None
    message: str | None = Field(default=None, min_length=1, max_length=500)
    recommended_action: str | None = Field(default=None, min_length=1, max_length=1000)


class AlertRuleResponse(AlertRuleFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    rule_id: str | None
    reading_id: int | None
    category: Literal["environmental", "device_health"]
    health_state: Literal["stale", "offline"] | None
    status: AlertStatus
    parameter: AlertParameter
    observed_value: float | None
    threshold: float | None
    unit: str
    severity: Severity
    message: str
    recommended_action: str
    created_at: datetime
    condition_started_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolution: str | None


class AlertPage(BaseModel):
    items: list[AlertResponse]
    total: int
    offset: int
    limit: int


class AlertActionRequest(BaseModel):
    actor: str | None = Field(default=None, max_length=120)


class DeviceStatusResponse(BaseModel):
    id: str
    name: str
    location: str | None
    status: Literal["online", "stale", "offline", "never_seen", "unknown"]
    last_seen_at: datetime | None
    last_successful_ingestion_at: datetime | None
    last_ingestion_error_at: datetime | None
    latest_reading: AirQualityReading | None
    health_alert: AlertResponse | None = None


class DevicePage(BaseModel):
    items: list[DeviceStatusResponse]
    total: int


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    channel: Literal["in_app", "browser", "email"]
    status: Literal["recorded", "pending", "delivered", "failed", "not_configured"]
    created_at: datetime
    attempted_at: datetime | None
    error: str | None


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    total: int
    offset: int
    limit: int
