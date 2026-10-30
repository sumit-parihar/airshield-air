"""Versioned alert, rule, notification, and device APIs."""

import hmac
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import Float, Integer, extract, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Alert, AlertRule, Device, NotificationRecord, Reading, RuleEvaluationState
from app.schemas import (
    AlertActionRequest,
    AlertPage,
    AlertResponse,
    AlertRuleCreate,
    AlertRuleFields,
    AlertRuleResponse,
    AlertRuleUpdate,
    AnalyticsSummary,
    DevicePage,
    DeviceStatusResponse,
    MetricStatistics,
    NotificationPage,
    NotificationResponse,
    PARAMETER_UNITS,
    ReadingPage,
    RuleExceedanceDuration,
    TrendPoint,
    TrendResponse,
)

router = APIRouter(prefix="/api/v1")


def _utc_period(start_at: datetime, end_at: datetime) -> tuple[datetime, datetime]:
    start = start_at.replace(tzinfo=timezone.utc) if start_at.tzinfo is None else start_at.astimezone(timezone.utc)
    end = end_at.replace(tzinfo=timezone.utc) if end_at.tzinfo is None else end_at.astimezone(timezone.utc)
    if start >= end:
        raise HTTPException(status_code=422, detail={"code": "invalid_time_range", "message": "start_at must be earlier than end_at"})
    return start, end


def _period_readings(session: Session, start: datetime, end: datetime, device_id: str | None):
    query = session.query(Reading).filter(Reading.timestamp >= start, Reading.timestamp < end)
    if device_id:
        query = query.filter(Reading.device_id == device_id)
    return query


def _rule_triggered(operator: str, value: float, threshold: float) -> bool:
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
    }[operator]


def _observed_exceedance_durations(
    session: Session,
    start: datetime,
    end: datetime,
    device_id: str | None,
) -> list[RuleExceedanceDuration]:
    """Count only bounded intervals with two consecutive abnormal persisted samples."""
    freshness = get_settings().reading_stale_after_seconds
    rules = session.query(AlertRule).filter(AlertRule.enabled.is_(True)).order_by(AlertRule.id).all()
    durations: list[RuleExceedanceDuration] = []
    for rule in rules:
        column = getattr(Reading, rule.parameter)
        query = session.query(Reading.device_id, Reading.ingestion_mode, Reading.timestamp, column, Reading.voc_unit).filter(
            Reading.timestamp >= start,
            Reading.timestamp < end,
        )
        if device_id:
            query = query.filter(Reading.device_id == device_id)
        rows = query.order_by(Reading.device_id, Reading.timestamp, Reading.id).all()
        totals: dict[str, list[int]] = {}
        previous: dict[tuple[str, str], tuple[datetime, bool]] = {}
        for row_device_id, mode, timestamp, value, voc_unit in rows:
            if mode not in ("live", "historical_backfill"):
                continue
            unit_matches = rule.parameter != "voc" or voc_unit == rule.unit
            is_abnormal = value is not None and unit_matches and _rule_triggered(rule.operator, value, rule.threshold)
            key = (row_device_id, mode)
            earlier = previous.get(key)
            if is_abnormal and earlier is not None and earlier[1]:
                previous_time = earlier[0]
                current_time = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
                previous_time = previous_time.replace(tzinfo=timezone.utc) if previous_time.tzinfo is None else previous_time.astimezone(timezone.utc)
                interval = int((current_time - previous_time).total_seconds())
                if 0 < interval <= freshness:
                    bucket = totals.setdefault(mode, [0, 0])
                    bucket[0] += interval
                    bucket[1] += 1
            current_time = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
            previous[key] = (current_time, is_abnormal)
        for mode, (seconds, intervals) in sorted(totals.items()):
            durations.append(RuleExceedanceDuration(
                rule_id=rule.id,
                parameter=rule.parameter,
                operator=rule.operator,
                threshold=rule.threshold,
                unit=rule.unit,
                ingestion_mode=mode,
                observed_seconds=seconds,
                observed_intervals=intervals,
            ))
    return durations


@router.get("/readings/history", response_model=ReadingPage)
def historical_readings(
    start_at: datetime,
    end_at: datetime,
    device_id: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_db),
) -> ReadingPage:
    start, end = _utc_period(start_at, end_at)
    query = _period_readings(session, start, end, device_id)
    total = query.count()
    items = query.order_by(Reading.timestamp, Reading.id).offset(offset).limit(limit).all()
    return ReadingPage(items=items, total=total, offset=offset, limit=limit)


@router.get("/analytics/summary", response_model=AnalyticsSummary)
def analytics_summary(
    start_at: datetime,
    end_at: datetime,
    device_id: str | None = Query(default=None, max_length=128),
    session: Session = Depends(get_db),
) -> AnalyticsSummary:
    start, end = _utc_period(start_at, end_at)
    query = _period_readings(session, start, end, device_id)
    reading_count = query.count()
    ingestion_mode_counts = {"live": 0, "historical_backfill": 0}
    for mode, count in query.with_entities(Reading.ingestion_mode, func.count(Reading.id)).group_by(Reading.ingestion_mode).all():
        ingestion_mode_counts[mode] = count
    metrics: dict[str, MetricStatistics] = {}
    metric_fields = {"co2": Reading.co2, "pm25": Reading.pm25, "voc": Reading.voc, "temperature": Reading.temperature, "humidity": Reading.humidity}
    for parameter, column in metric_fields.items():
        result = query.with_entities(func.count(column), func.min(column), func.max(column), func.avg(column)).one()
        unit = PARAMETER_UNITS.get(parameter)
        if parameter == "voc":
            units = [row[0] for row in query.with_entities(Reading.voc_unit).filter(column.is_not(None)).distinct().all()]
            unit = units[0] if len(units) == 1 else "mixed" if len(units) > 1 else None
        metrics[parameter] = MetricStatistics(
            count=result[0],
            minimum=result[1] if unit != "mixed" else None,
            maximum=result[2] if unit != "mixed" else None,
            average=float(result[3]) if result[3] is not None and unit != "mixed" else None,
            unit=unit,
        )

    alert_query = session.query(Alert).filter(Alert.created_at >= start, Alert.created_at < end)
    if device_id:
        alert_query = alert_query.filter(Alert.device_id == device_id)
    by_severity = {key: 0 for key in ("info", "warning", "critical")}
    by_parameter = {key: 0 for key in metric_fields}
    for severity, count in alert_query.with_entities(Alert.severity, func.count(Alert.id)).group_by(Alert.severity).all():
        by_severity[severity] = count
    for parameter, count in alert_query.with_entities(Alert.parameter, func.count(Alert.id)).group_by(Alert.parameter).all():
        by_parameter[parameter] = count
    return AnalyticsSummary(
        start_at=start,
        end_at=end,
        device_id=device_id,
        reading_count=reading_count,
        ingestion_mode_counts=ingestion_mode_counts,
        metrics=metrics,
        alert_counts_by_severity=by_severity,
        alert_counts_by_parameter=by_parameter,
        exceedance_durations=_observed_exceedance_durations(session, start, end, device_id),
    )


@router.get("/analytics/trends", response_model=TrendResponse)
def analytics_trends(
    start_at: datetime,
    end_at: datetime,
    parameter: str = Query(pattern="^(co2|pm25|voc|temperature|humidity)$"),
    device_id: str | None = Query(default=None, max_length=128),
    points: int = Query(default=500, ge=2, le=1000),
    session: Session = Depends(get_db),
) -> TrendResponse:
    start, end = _utc_period(start_at, end_at)
    column = getattr(Reading, parameter)
    query = session.query(Reading).filter(
        Reading.timestamp >= start,
        Reading.timestamp < end,
        column.is_not(None),
    )
    if device_id:
        query = query.filter(Reading.device_id == device_id)
    unit = PARAMETER_UNITS.get(parameter)
    if parameter == "voc":
        units = [row[0] for row in query.with_entities(Reading.voc_unit).distinct().all()]
        if len(units) > 1:
            raise HTTPException(status_code=409, detail={"code": "mixed_voc_units", "message": "VOC values with different or unknown units cannot be combined in one trend"})
        unit = units[0] if units else None
    ingestion_modes = [
        mode
        for (mode,) in query.with_entities(Reading.ingestion_mode).distinct().order_by(Reading.ingestion_mode).all()
    ]

    interval_seconds = max(1, math.ceil((end - start).total_seconds() / points))
    if session.get_bind().dialect.name == "postgresql":
        epoch = extract("epoch", Reading.timestamp)
        bucket = func.floor(epoch / interval_seconds)
    else:
        epoch = func.cast(func.strftime("%s", Reading.timestamp), Float)
        bucket = func.cast(epoch / interval_seconds, Integer)
    rows = (
        query.with_entities(func.min(Reading.timestamp), func.avg(column), func.count(Reading.id))
        .group_by(bucket)
        .order_by(func.min(Reading.timestamp))
        .all()
    )
    samples = sum(row[2] for row in rows)
    return TrendResponse(
        parameter=parameter,
        unit=unit,
        interval_seconds=interval_seconds,
        samples_in_range=samples,
        ingestion_modes=ingestion_modes,
        points=[TrendPoint(timestamp=row[0], value=float(row[1]), sample_count=row[2]) for row in rows],
    )


def require_admin(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    token = settings.alerts_admin_token
    if token is None:
        if settings.app_env.lower() == "production":
            raise HTTPException(status_code=503, detail={"code": "admin_auth_not_configured", "message": "Administrative authentication is not configured"})
        return
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "Administrative authorization is required"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _alert_or_404(session: Session, alert_id: int) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail={"code": "alert_not_found", "message": "Alert was not found"})
    return alert


def _rule_or_404(session: Session, rule_id: str) -> AlertRule:
    rule = session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail={"code": "rule_not_found", "message": "Alert rule was not found"})
    return rule


@router.get("/alerts", response_model=AlertPage)
def list_alerts(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(active|acknowledged|resolved)$"),
    severity: str | None = Query(default=None, pattern="^(info|warning|critical)$"),
    parameter: str | None = Query(default=None, pattern="^(co2|pm25|voc|temperature|humidity|device_health)$"),
    device_id: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> AlertPage:
    query = session.query(Alert)
    if status_filter:
        query = query.filter(Alert.status == status_filter)
    if severity:
        query = query.filter(Alert.severity == severity)
    if parameter:
        query = query.filter(Alert.parameter == parameter)
    if device_id:
        query = query.filter(Alert.device_id == device_id)
    total = query.count()
    items = query.order_by(Alert.created_at.desc(), Alert.id.desc()).offset(offset).limit(limit).all()
    return AlertPage(items=items, total=total, offset=offset, limit=limit)


@router.get("/alerts/active", response_model=AlertPage)
def list_active_alerts(
    severity: str | None = Query(default=None, pattern="^(info|warning|critical)$"),
    parameter: str | None = Query(default=None, pattern="^(co2|pm25|voc|temperature|humidity|device_health)$"),
    device_id: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> AlertPage:
    query = session.query(Alert).filter(Alert.status.in_(("active", "acknowledged")))
    if severity:
        query = query.filter(Alert.severity == severity)
    if parameter:
        query = query.filter(Alert.parameter == parameter)
    if device_id:
        query = query.filter(Alert.device_id == device_id)
    total = query.count()
    items = query.order_by(Alert.created_at.desc(), Alert.id.desc()).offset(offset).limit(limit).all()
    return AlertPage(items=items, total=total, offset=offset, limit=limit)


@router.get("/alerts/{alert_id}", response_model=AlertResponse)
def get_alert(alert_id: int, session: Session = Depends(get_db)) -> Alert:
    return _alert_or_404(session, alert_id)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertResponse, dependencies=[Depends(require_admin)])
def acknowledge_alert(
    alert_id: int,
    request: AlertActionRequest,
    session: Session = Depends(get_db),
) -> Alert:
    alert = _alert_or_404(session, alert_id)
    if alert.status == "resolved":
        raise HTTPException(status_code=409, detail={"code": "alert_resolved", "message": "A resolved alert cannot be acknowledged"})
    if alert.status == "active":
        alert.status = "acknowledged"
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by = request.actor
        session.commit()
        session.refresh(alert)
    return alert


@router.post("/alerts/{alert_id}/resolve", response_model=AlertResponse, dependencies=[Depends(require_admin)])
def resolve_alert(
    alert_id: int,
    request: AlertActionRequest,
    session: Session = Depends(get_db),
) -> Alert:
    alert = _alert_or_404(session, alert_id)
    if alert.status != "resolved":
        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolution = "manual"
        if request.actor and not alert.acknowledged_by:
            alert.acknowledged_by = request.actor
        session.commit()
        session.refresh(alert)
    return alert


@router.get("/alert-rules", response_model=list[AlertRuleResponse])
def list_alert_rules(
    parameter: str | None = Query(default=None, pattern="^(co2|pm25|voc|temperature|humidity)$"),
    enabled: bool | None = None,
    session: Session = Depends(get_db),
) -> list[AlertRule]:
    query = session.query(AlertRule)
    if parameter:
        query = query.filter(AlertRule.parameter == parameter)
    if enabled is not None:
        query = query.filter(AlertRule.enabled.is_(enabled))
    return query.order_by(AlertRule.created_at, AlertRule.id).all()


@router.post("/alert-rules", response_model=AlertRuleResponse, status_code=201, dependencies=[Depends(require_admin)])
def create_alert_rule(request: AlertRuleCreate, session: Session = Depends(get_db)) -> AlertRule:
    rule = AlertRule(**request.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.get("/alert-rules/{rule_id}", response_model=AlertRuleResponse)
def get_alert_rule(rule_id: str, session: Session = Depends(get_db)) -> AlertRule:
    return _rule_or_404(session, rule_id)


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleResponse, dependencies=[Depends(require_admin)])
def update_alert_rule(rule_id: str, request: AlertRuleUpdate, session: Session = Depends(get_db)) -> AlertRule:
    rule = _rule_or_404(session, rule_id)
    values = {
        "parameter": rule.parameter,
        "operator": rule.operator,
        "threshold": rule.threshold,
        "unit": rule.unit,
        "severity": rule.severity,
        "enabled": rule.enabled,
        "persistence_seconds": rule.persistence_seconds,
        "recovery_threshold": rule.recovery_threshold,
        "message": rule.message,
        "recommended_action": rule.recommended_action,
    }
    updates = request.model_dump(exclude_unset=True)
    required_values = {"parameter", "operator", "threshold", "unit", "severity", "enabled", "persistence_seconds", "message", "recommended_action"}
    if any(key in required_values and value is None for key, value in updates.items()):
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "A non-null rule field was set to null"})
    values.update(updates)
    try:
        validated = AlertRuleFields.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "Rule configuration is invalid", "errors": jsonable_encoder(exc.errors())},
        ) from exc
    sensitive_fields = {"parameter", "operator", "threshold", "unit", "recovery_threshold"}
    changed_sensitive_fields = any(values[field] != getattr(rule, field) for field in sensitive_fields)
    unresolved = session.query(Alert.id).filter(
        Alert.rule_id == rule.id,
        Alert.status.in_(("active", "acknowledged")),
    ).first()
    if changed_sensitive_fields and unresolved:
        raise HTTPException(
            status_code=409,
            detail={"code": "rule_has_active_alerts", "message": "Resolve this rule's active alerts before changing its trigger or recovery configuration"},
        )
    if changed_sensitive_fields:
        session.query(RuleEvaluationState).filter_by(rule_id=rule.id).delete(synchronize_session=False)
    for key, value in validated.model_dump().items():
        setattr(rule, key, value)
    session.commit()
    session.refresh(rule)
    return rule


@router.get("/notifications/in-app", response_model=NotificationPage)
def list_in_app_notifications(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> NotificationPage:
    query = session.query(NotificationRecord).filter(NotificationRecord.channel == "in_app")
    total = query.count()
    items = query.order_by(NotificationRecord.created_at.desc(), NotificationRecord.id.desc()).offset(offset).limit(limit).all()
    return NotificationPage(items=items, total=total, offset=offset, limit=limit)


def _device_status(device: Device, now: datetime, freshness_seconds: int) -> str:
    if device.last_ingestion_error_at is not None:
        failed_at = device.last_ingestion_error_at
        if failed_at.tzinfo is None:
            failed_at = failed_at.replace(tzinfo=timezone.utc)
        succeeded_at = device.last_successful_ingestion_at
        if succeeded_at is None or failed_at > (succeeded_at.replace(tzinfo=timezone.utc) if succeeded_at.tzinfo is None else succeeded_at):
            return "unknown"
    if device.last_successful_ingestion_at is None:
        return "never_seen"
    if device.last_seen_at is None:
        return "never_seen"
    seen_at = device.last_seen_at
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=timezone.utc)
    age = (now - seen_at).total_seconds()
    if age <= freshness_seconds:
        return "online"
    if age <= freshness_seconds * 2:
        return "stale"
    return "offline"


def _device_response(session: Session, device: Device, now: datetime, freshness: int) -> DeviceStatusResponse:
    latest = (
        session.query(Reading)
        .filter(Reading.device_id == device.id, Reading.ingestion_mode == "live")
        .order_by(Reading.timestamp.desc(), Reading.id.desc())
        .first()
    )
    # Older versions could have advanced last_seen_at during backfill. Derive
    # public health from live-origin rows so historical imports cannot look live.
    health_device = Device(
        id=device.id,
        name=device.name,
        last_seen_at=latest.timestamp if latest is not None else None,
        last_successful_ingestion_at=device.last_successful_ingestion_at,
        last_ingestion_error_at=device.last_ingestion_error_at,
    )
    health_alert = session.query(Alert).filter(
        Alert.device_id == device.id,
        Alert.category == "device_health",
        Alert.status.in_(("active", "acknowledged")),
    ).order_by(Alert.created_at.desc(), Alert.id.desc()).first()
    return DeviceStatusResponse(
        id=device.id,
        name=device.name,
        location=device.location,
        status=_device_status(health_device, now, freshness),
        last_seen_at=latest.timestamp if latest is not None else None,
        last_successful_ingestion_at=device.last_successful_ingestion_at,
        last_ingestion_error_at=device.last_ingestion_error_at,
        latest_reading=latest,
        health_alert=health_alert,
    )


@router.get("/devices", response_model=DevicePage)
def list_devices(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> DevicePage:
    query = session.query(Device)
    total = query.count()
    devices = query.order_by(Device.name, Device.id).offset(offset).limit(limit).all()
    settings = get_settings()
    now = datetime.now(timezone.utc)
    items = [_device_response(session, device, now, settings.reading_stale_after_seconds) for device in devices]
    return DevicePage(items=items, total=total)


@router.get("/devices/{device_id}/status", response_model=DeviceStatusResponse)
def get_device_status(device_id: str, session: Session = Depends(get_db)) -> DeviceStatusResponse:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail={"code": "device_not_found", "message": "Device was not found"})
    settings = get_settings()
    return _device_response(session, device, datetime.now(timezone.utc), settings.reading_stale_after_seconds)


@router.get("/devices/{device_id}", response_model=DeviceStatusResponse)
def get_device(device_id: str, session: Session = Depends(get_db)) -> DeviceStatusResponse:
    return get_device_status(device_id, session)
