"""Deterministic rule evaluation for persisted readings."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, sessionmaker

from app.models import Alert, AlertRule, Reading, RuleEvaluationState
from app.notifications import NotificationService, utc_now

PARAMETER_FIELDS = ("co2", "pm25", "voc", "temperature", "humidity")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _triggered(operator: str, value: float, threshold: float) -> bool:
    return {"gt": value > threshold, "gte": value >= threshold, "lt": value < threshold, "lte": value <= threshold}[operator]


def _recovered(rule: AlertRule, value: float) -> bool:
    high_rule = rule.operator in ("gt", "gte")
    if rule.recovery_threshold is not None:
        return value <= rule.recovery_threshold if high_rule else value >= rule.recovery_threshold
    if rule.operator == "gt":
        return value <= rule.threshold
    if rule.operator == "gte":
        return value < rule.threshold
    if rule.operator == "lt":
        return value >= rule.threshold
    return value > rule.threshold


def evaluate_reading(
    session_factory: sessionmaker[Session],
    reading_id: int,
    *,
    freshness_seconds: int = 900,
    evaluated_at: datetime | None = None,
) -> int:
    """Evaluate one persisted reading atomically and return the number of new alerts."""
    created_count = 0
    now = evaluated_at or utc_now()
    with session_factory() as session:
        with session.begin():
            reading = session.get(Reading, reading_id)
            if reading is None:
                raise ValueError("Reading does not exist")
            if reading.ingestion_mode != "live":
                return 0
            reading_time = _utc(reading.timestamp)
            rules = (
                session.query(AlertRule)
                .filter(AlertRule.parameter.in_(PARAMETER_FIELDS))
                .order_by(AlertRule.id)
                .all()
            )
            for rule in rules:
                value = getattr(reading, rule.parameter)
                if value is None:
                    continue

                alert = (
                    session.query(Alert)
                    .filter(
                        Alert.device_id == reading.device_id,
                        Alert.rule_id == rule.id,
                        Alert.status.in_(("active", "acknowledged")),
                    )
                    .one_or_none()
                )
                if alert is not None:
                    if _recovered(rule, value):
                        alert.status = "resolved"
                        alert.resolved_at = reading_time
                        alert.resolution = "condition_recovered"
                    continue

                state = (
                    session.query(RuleEvaluationState)
                    .filter_by(device_id=reading.device_id, rule_id=rule.id)
                    .one_or_none()
                )
                if not rule.enabled:
                    if state is not None:
                        session.delete(state)
                    continue

                if not _triggered(rule.operator, value, rule.threshold):
                    if state is not None:
                        session.delete(state)
                    continue

                if state is not None and reading.id == state.last_reading_id:
                    continue
                if state is not None and reading_time <= _utc(state.last_triggered_at):
                    continue
                if state is not None and reading_time - _utc(state.last_triggered_at) > timedelta(seconds=freshness_seconds):
                    state.first_triggered_at = reading_time
                    state.sample_count = 1
                elif state is not None:
                    state.sample_count += 1

                if state is None:
                    state = RuleEvaluationState(
                        device_id=reading.device_id,
                        rule_id=rule.id,
                        first_triggered_at=reading_time,
                        last_triggered_at=reading_time,
                        last_reading_id=reading.id,
                        sample_count=1,
                    )
                    session.add(state)
                else:
                    state.last_triggered_at = reading_time
                    state.last_reading_id = reading.id

                elapsed = (reading_time - _utc(state.first_triggered_at)).total_seconds()
                if elapsed < rule.persistence_seconds or (rule.persistence_seconds > 0 and state.sample_count < 2):
                    continue

                alert = Alert(
                    device_id=reading.device_id,
                    rule_id=rule.id,
                    reading_id=reading.id,
                    status="active",
                    parameter=rule.parameter,
                    observed_value=value,
                    threshold=rule.threshold,
                    unit=rule.unit,
                    severity=rule.severity,
                    message=rule.message,
                    recommended_action=rule.recommended_action,
                    created_at=_utc(now),
                    condition_started_at=_utc(state.first_triggered_at),
                )
                session.add(alert)
                session.flush()
                NotificationService.record_in_app(session, alert)
                session.delete(state)
                created_count += 1
    return created_count
