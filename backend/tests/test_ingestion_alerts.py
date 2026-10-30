import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app import api
from app.alert_engine import evaluate_reading
from app.config import Settings
from app.database import Base, get_db
from app.ingestion import ThingSpeakIngestionService
from app.main import create_app
from app.models import Alert, AlertRule, Device, NotificationRecord, Reading, RuleEvaluationState
from app.notifications import NotificationService
from app.schemas import AirQualityReading
from app.thingspeak import ThingSpeakError


@pytest.fixture
def store(tmp_path):
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(tmp_path / "airshield-test.db")))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def make_rule(session, *, id="co2-high", parameter="co2", operator="gt", threshold=1000, unit="ppm", persistence=0, recovery=None, enabled=True):
    rule = AlertRule(
        id=id,
        parameter=parameter,
        operator=operator,
        threshold=threshold,
        unit=unit,
        severity="warning",
        enabled=enabled,
        persistence_seconds=persistence,
        recovery_threshold=recovery,
        message=f"{parameter} is outside the configured project threshold",
        recommended_action="Review ventilation and verify sensor readings.",
    )
    session.add(rule)
    return rule


def add_reading(store, timestamp, *, device="device-1", co2=None, pm25=None, voc=None, temperature=None, humidity=None, entry=1, ingestion_mode="live"):
    with store() as session:
        with session.begin():
            if session.get(Device, device) is None:
                session.add(Device(id=device, name=device))
            row = Reading(
                device_id=device,
                provider_entry_id=entry,
                provider_entry_key=f"{device}:{entry}",
                timestamp=timestamp,
                co2=co2,
                pm25=pm25,
                voc=voc,
                voc_unit="sensor-index" if voc is not None else None,
                temperature=temperature,
                humidity=humidity,
                source="thingspeak",
                source_metadata={},
                ingestion_mode=ingestion_mode,
            )
            session.add(row)
            session.flush()
            return row.id


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_batches = []

    async def recent(self, count):
        self.requested_batches.append(count)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def reading(timestamp, *, entry=None, **values):
    return AirQualityReading(
        provider_entry_id=entry,
        device_id="thingspeak-3509505",
        timestamp=timestamp,
        source_metadata={"channel_id": 3509505},
        **values,
    )


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def test_ingestion_orders_normalizes_and_persists_feeds(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    client = FakeClient([
        [reading(now, entry=3, co2=1200), reading(now - timedelta(seconds=20), entry=2, pm25=25)],
    ])
    service = ThingSpeakIngestionService(
        settings(thingspeak_poll_interval_seconds=15, thingspeak_batch_size=20),
        store,
        client,
        clock=lambda: now,
    )
    result = asyncio.run(service.poll_once())
    with store() as session:
        rows = session.scalars(select(Reading).order_by(Reading.id)).all()
        device = session.get(Device, "thingspeak-3509505")
    assert result.request_succeeded and result.received == 2 and result.inserted == 2
    assert client.requested_batches == [20]
    assert [row.provider_entry_id for row in rows] == [2, 3]
    assert rows[0].pm25 == 25 and rows[0].co2 is None
    assert as_utc(device.last_seen_at) == now and as_utc(device.last_successful_ingestion_at) == now


def test_duplicate_ingestion_is_prevented_for_entry_ids_and_missing_ids(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    one = reading(now, entry=22, co2=800)
    no_id = reading(now - timedelta(seconds=1), entry=None, humidity=44)
    client = FakeClient([[one, no_id], [one, no_id]])
    service = ThingSpeakIngestionService(settings(), store, client, clock=lambda: now)
    first = asyncio.run(service.poll_once())
    second = asyncio.run(service.poll_once())
    with store() as session:
        count = len(session.scalars(select(Reading.id)).all())
    assert first.inserted == 2 and second.inserted == 0 and second.duplicates == 2
    assert count == 2


def test_empty_feeds_and_stale_feeds_are_handled_without_fabrication(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    stale = reading(now - timedelta(hours=1), entry=7, co2=9999)
    client = FakeClient([[], [stale]])
    service = ThingSpeakIngestionService(settings(reading_stale_after_seconds=60), store, client, clock=lambda: now)
    empty = asyncio.run(service.poll_once())
    stale_result = asyncio.run(service.poll_once())
    with store() as session:
        rows = session.scalars(select(Reading)).all()
        device = session.get(Device, "thingspeak-3509505")
    assert empty.request_succeeded and empty.received == empty.inserted == 0
    assert stale_result.stale_skipped == 1 and not rows
    assert as_utc(device.last_successful_ingestion_at) == now
    assert as_utc(device.last_seen_at) == stale.timestamp


def test_live_freshness_boundary_is_inclusive_and_older_feeds_are_skipped(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    cutoff = now - timedelta(seconds=900)
    client = FakeClient([[reading(cutoff - timedelta(microseconds=1), entry=1, co2=450), reading(cutoff, entry=2, co2=460)]])
    evaluated = []
    service = ThingSpeakIngestionService(
        settings(reading_stale_after_seconds=900), store, client,
        clock=lambda: now,
        evaluator=lambda factory, reading_id, **kwargs: evaluated.append(reading_id) or 0,
    )
    result = asyncio.run(service.poll_once())
    with store() as session:
        rows = session.scalars(select(Reading).order_by(Reading.timestamp)).all()
    assert result.received == 2 and result.inserted == 1 and result.stale_skipped == 1
    assert len(rows) == 1 and as_utc(rows[0].timestamp) == cutoff
    assert rows[0].ingestion_mode == "live" and evaluated == [rows[0].id]


def test_historical_backfill_preserves_stale_timestamps_deduplicates_and_skips_alerts(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    historical = reading(now - timedelta(days=27), entry=77, co2=1500)
    client = FakeClient([[historical], [historical], [historical]])
    evaluator_calls = []
    service = ThingSpeakIngestionService(
        settings(reading_stale_after_seconds=900), store, client,
        clock=lambda: now,
        evaluator=lambda *args, **kwargs: evaluator_calls.append(args) or 0,
    )
    with store() as session:
        with session.begin():
            session.add(Device(id="thingspeak-3509505", name="ThingSpeak"))
            make_rule(session, threshold=1000)

    first = asyncio.run(service.backfill_history_once(8000))
    second = asyncio.run(service.backfill_history_once(8000))
    with store() as session:
        assert session.get(Device, "thingspeak-3509505").last_successful_ingestion_at is None
    live_poll = asyncio.run(service.poll_once())
    with store() as session:
        rows = session.scalars(select(Reading)).all()
        alerts = session.scalars(select(Alert)).all()
        row = rows[0]
        device = session.get(Device, "thingspeak-3509505")
    assert first.inserted == 1 and second.inserted == 0 and second.duplicates == 1
    assert live_poll.inserted == 0 and live_poll.stale_skipped == 1
    assert len(rows) == 1 and row.ingestion_mode == "historical_backfill"
    assert as_utc(row.timestamp) == historical.timestamp and row.alerts_evaluated_at is None
    assert as_utc(device.last_successful_ingestion_at) == now
    assert not alerts and not evaluator_calls
    # Guard the alert engine itself too, in case a caller explicitly submits the backfill ID.
    assert evaluate_reading(store, row.id, evaluated_at=now) == 0
    with store() as session:
        assert session.scalar(select(func.count(Alert.id))) == 0


def test_upstream_failure_does_not_stop_later_poll(store):
    now = datetime.now(timezone.utc)
    client = FakeClient([ThingSpeakError("unavailable"), [reading(now, entry=1, co2=400)]])
    service = ThingSpeakIngestionService(settings(), store, client)
    failed = asyncio.run(service.poll_once())
    recovered = asyncio.run(service.poll_once())
    assert not failed.request_succeeded
    assert recovered.request_succeeded and recovered.inserted == 1


def test_failed_alert_evaluation_keeps_persisted_reading_pending_for_retry(store):
    now = datetime.now(timezone.utc)
    attempts = 0

    def flaky_evaluator(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("evaluation unavailable")
        return 0

    service = ThingSpeakIngestionService(
        settings(),
        store,
        FakeClient([[reading(now, entry=5, co2=510)], []]),
        clock=lambda: now,
        evaluator=flaky_evaluator,
    )
    first = asyncio.run(service.poll_once())
    with store() as session:
        persisted = session.scalar(select(Reading))
        assert persisted is not None and persisted.alerts_evaluated_at is None
        assert persisted.alert_evaluation_error == "RuntimeError"
    second = asyncio.run(service.poll_once())
    with store() as session:
        persisted = session.scalar(select(Reading))
    assert first.inserted == 1 and first.evaluation_failures == 1
    assert second.inserted == 0 and second.evaluation_failures == 0
    assert persisted.alerts_evaluated_at is not None and persisted.alert_evaluation_error is None


def test_device_freshness_states_are_explicit(store):
    from app.api import _device_status

    now = datetime.now(timezone.utc)
    device = Device(id="d", name="sensor", last_successful_ingestion_at=now, last_seen_at=now)
    assert _device_status(device, now, 60) == "online"
    device.last_seen_at = now - timedelta(seconds=61)
    assert _device_status(device, now, 60) == "stale"
    device.last_seen_at = now - timedelta(seconds=121)
    device.last_successful_ingestion_at = now - timedelta(seconds=121)
    assert _device_status(device, now, 60) == "offline"
    device.last_successful_ingestion_at = now
    device.last_seen_at = None
    assert _device_status(device, now, 60) == "never_seen"


def test_device_health_stale_offline_deduplicates_and_recovers(store):
    start = datetime.now(timezone.utc)
    feeds = reading(start, entry=101, co2=700)
    service = ThingSpeakIngestionService(settings(reading_stale_after_seconds=60), store, FakeClient([[feeds], [feeds], [feeds], [feeds], [feeds], [reading(start + timedelta(seconds=180), entry=102, co2=710)]]), clock=lambda: start)
    asyncio.run(service.poll_once())
    service.clock = lambda: start + timedelta(seconds=60)
    asyncio.run(service.poll_once())
    with store() as session:
        assert session.query(Alert).filter_by(category="device_health").count() == 0
    service.clock = lambda: start + timedelta(seconds=61)
    asyncio.run(service.poll_once())
    with store() as session:
        alerts = session.query(Alert).filter_by(category="device_health").all()
        assert [(a.health_state, a.status) for a in alerts] == [("stale", "active")]
        assert alerts[0].rule_id is None and alerts[0].reading_id is None
        assert alerts[0].observed_value is None and alerts[0].threshold is None
        assert session.query(NotificationRecord).filter_by(alert_id=alerts[0].id).count() == 1
    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        listed = client.get("/api/v1/alerts/active?parameter=device_health")
        device_status = client.get("/api/v1/devices/thingspeak-3509505/status")
    assert listed.status_code == 200 and listed.json()["items"][0]["category"] == "device_health"
    assert listed.json()["items"][0]["observed_value"] is None
    assert device_status.status_code == 200
    assert device_status.json()["health_alert"]["health_state"] == "stale"
    service.clock = lambda: start + timedelta(seconds=120)
    asyncio.run(service.poll_once())
    with store() as session:
        assert session.query(Alert).filter_by(category="device_health", health_state="offline").count() == 0
    service.clock = lambda: start + timedelta(seconds=121)
    asyncio.run(service.poll_once())
    with store() as session:
        alerts = session.query(Alert).filter_by(category="device_health").order_by(Alert.id).all()
        assert [(a.health_state, a.status) for a in alerts] == [("stale", "resolved"), ("offline", "active")]
    service.clock = lambda: start + timedelta(seconds=180)
    asyncio.run(service.poll_once())
    with store() as session:
        alerts = session.query(Alert).filter_by(category="device_health").order_by(Alert.id).all()
        device = session.get(Device, "thingspeak-3509505")
        assert [a.status for a in alerts] == ["resolved", "resolved"]
        assert [a.resolution for a in alerts] == ["health_state_changed", "condition_recovered"]
        assert device.health_state == "online"


def test_device_health_state_persists_across_service_restart_without_duplicates(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    initial = reading(now, entry=201, co2=600)
    old = initial
    first = ThingSpeakIngestionService(settings(reading_stale_after_seconds=60), store, FakeClient([[initial]]), clock=lambda: now)
    asyncio.run(first.poll_once())
    second = ThingSpeakIngestionService(settings(reading_stale_after_seconds=60), store, FakeClient([[old]]), clock=lambda: now + timedelta(seconds=61))
    asyncio.run(second.poll_once())
    with store() as session:
        assert session.query(Alert).filter_by(category="device_health", health_state="stale").count() == 1
        assert session.get(Device, "thingspeak-3509505").health_state == "stale"


def test_provider_outage_is_unknown_and_does_not_transition_health_alert(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    old = reading(now, entry=301, co2=600)
    client = FakeClient([[old], ThingSpeakError("upstream failed")])
    service = ThingSpeakIngestionService(settings(reading_stale_after_seconds=60), store, client, clock=lambda: now)
    asyncio.run(service.poll_once())
    service.clock = lambda: now + timedelta(seconds=180)
    result = asyncio.run(service.poll_once())
    with store() as session:
        device = session.get(Device, "thingspeak-3509505")
        assert not result.request_succeeded
        assert device.health_state == "online"
        assert as_utc(device.last_ingestion_error_at) == now + timedelta(seconds=180)
        assert session.query(Alert).filter_by(category="device_health").count() == 0
        from app.api import _device_status
        assert _device_status(device, now + timedelta(seconds=180), 60) == "unknown"
    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        response = client.get("/api/v1/devices/thingspeak-3509505/status")
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["last_ingestion_error_at"] is not None


def test_initial_provider_outage_registers_configured_device_as_unknown(store):
    now = datetime.now(timezone.utc)
    service = ThingSpeakIngestionService(settings(), store, FakeClient([ThingSpeakError("unavailable")]), clock=lambda: now)
    result = asyncio.run(service.poll_once())
    with store() as session:
        device = session.get(Device, "thingspeak-3509505")
        assert not result.request_succeeded
        assert device is not None and device.name == "AirShield ThingSpeak device"
        assert as_utc(device.last_ingestion_error_at) == now
        assert device.health_state is None
        assert session.query(Alert).filter_by(category="device_health").count() == 0


def test_backfill_does_not_change_device_health_or_alerts(store):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    live = reading(now, entry=401, co2=600)
    historical = reading(now - timedelta(days=1), entry=402, co2=1600)
    service = ThingSpeakIngestionService(settings(reading_stale_after_seconds=60), store, FakeClient([[live], [live]]), clock=lambda: now)
    asyncio.run(service.poll_once())
    service.clock = lambda: now + timedelta(seconds=61)
    asyncio.run(service.poll_once())
    with store() as session:
        device = session.get(Device, "thingspeak-3509505")
        before = (device.last_seen_at, device.last_successful_ingestion_at, device.health_state, device.health_state_since)
        active_before = session.query(Alert).filter_by(category="device_health", status="active").count()
        assert active_before == 1
    service.clock = lambda: now + timedelta(seconds=180)
    service._persist_feeds([historical], ingestion_mode="historical_backfill", skip_stale=False, evaluate=False)
    with store() as session:
        device = session.get(Device, "thingspeak-3509505")
        after = (device.last_seen_at, device.last_successful_ingestion_at, device.health_state, device.health_state_since)
        assert before == after
        assert session.query(Alert).filter_by(category="device_health", status="active").count() == active_before


def test_threshold_crossing_dedup_and_in_app_notification(store):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session)
    first_id = add_reading(store, now, co2=1200, entry=1)
    second_id = add_reading(store, now + timedelta(seconds=10), co2=1300, entry=2)
    assert evaluate_reading(store, first_id) == 1
    assert evaluate_reading(store, first_id) == 0
    assert evaluate_reading(store, second_id) == 0
    with store() as session:
        alerts = session.scalars(select(Alert)).all()
        notes = session.scalars(select(NotificationRecord)).all()
    assert len(alerts) == len(notes) == 1
    assert alerts[0].observed_value == 1200
    assert alerts[0].status == "active"
    assert as_utc(alerts[0].condition_started_at) == now
    assert notes[0].channel == "in_app" and notes[0].status == "recorded"


def test_persistence_requires_two_sustained_samples(store):
    start = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session, persistence=30)
    ids = [
        add_reading(store, start, co2=1100, entry=1),
        add_reading(store, start + timedelta(seconds=20), co2=1120, entry=2),
        add_reading(store, start + timedelta(seconds=35), co2=1150, entry=3),
    ]
    assert [evaluate_reading(store, item) for item in ids] == [0, 0, 1]
    with store() as session:
        alert = session.scalar(select(Alert))
        assert session.scalar(select(RuleEvaluationState)) is None
    assert as_utc(alert.condition_started_at) == start and alert.observed_value == 1150


def test_multiple_parameters_and_missing_values(store):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session, id="pm-high", parameter="pm25", threshold=15, unit="µg/m³")
            make_rule(session, id="voc-high", parameter="voc", threshold=200, unit="sensor-index")
            make_rule(session, id="temp-high", parameter="temperature", threshold=30, unit="°C")
    row_id = add_reading(store, now, co2=None, pm25=22, voc=250, temperature=None)
    assert evaluate_reading(store, row_id) == 2
    with store() as session:
        alerts = session.scalars(select(Alert).order_by(Alert.parameter)).all()
    assert [alert.parameter for alert in alerts] == ["pm25", "voc"]


def test_hysteresis_resolves_after_recovery_and_acknowledged_state(store):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session, threshold=1000, recovery=900)
    initial = add_reading(store, now, co2=1200, entry=1)
    within_hysteresis = add_reading(store, now + timedelta(seconds=10), co2=950, entry=2)
    recovered = add_reading(store, now + timedelta(seconds=20), co2=900, entry=3)
    evaluate_reading(store, initial)
    with store() as session:
        alert = session.scalar(select(Alert))
        alert.status = "acknowledged"
        alert.acknowledged_at = now
        session.commit()
    assert evaluate_reading(store, within_hysteresis) == 0
    with store() as session:
        assert session.scalar(select(Alert)).status == "acknowledged"
    assert evaluate_reading(store, recovered) == 0
    with store() as session:
        alert = session.scalar(select(Alert))
    assert alert.status == "resolved" and alert.resolution == "condition_recovered"
    assert as_utc(alert.resolved_at) == now + timedelta(seconds=20)


def test_notification_record_creation_is_idempotent(store):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session)
    row_id = add_reading(store, now, co2=1200)
    evaluate_reading(store, row_id)
    with store() as session:
        alert = session.scalar(select(Alert))
        first = NotificationService.record_in_app(session, alert)
        session.flush()
        second = NotificationService.record_in_app(session, alert)
        assert first.id == second.id
        assert len(session.scalars(select(NotificationRecord)).all()) == 1


@pytest.mark.asyncio
async def test_external_notification_delivery_records_failure_and_confirmation(store):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session)
    reading_id = add_reading(store, now, co2=1200)
    evaluate_reading(store, reading_id)
    with store() as session:
        alert_id = session.scalar(select(Alert.id))

    class Adapter:
        channel = "email"
        fail = True
        async def deliver(self, alert):
            if self.fail:
                raise RuntimeError("private provider response")

    adapter = Adapter()
    failed = await NotificationService.deliver(store, alert_id, adapter)
    assert failed.status == "failed" and failed.attempted_at is not None
    assert failed.error == "RuntimeError" and "private" not in failed.error
    adapter.fail = False
    delivered = await NotificationService.deliver(store, alert_id, adapter)
    assert delivered.status == "delivered" and delivered.error is None
    assert await NotificationService.deliver(store, alert_id, adapter)
    with store() as session:
        assert len(session.scalars(select(NotificationRecord).where(NotificationRecord.channel == "email")).all()) == 1


def test_rule_and_alert_api_pagination_validation_and_ack(store):
    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        created = client.post("/api/v1/alert-rules", json={
            "parameter": "co2", "operator": "gt", "threshold": 1000, "unit": "ppm",
            "severity": "warning", "message": "CO2 high", "recommended_action": "Ventilate",
        })
        assert created.status_code == 201
        assert client.patch(f"/api/v1/alert-rules/{created.json()['id']}", json={"threshold": None}).status_code == 422
        disabled = client.patch(f"/api/v1/alert-rules/{created.json()['id']}", json={"enabled": False})
        assert disabled.status_code == 200 and disabled.json()["enabled"] is False
        assert client.patch(f"/api/v1/alert-rules/{created.json()['id']}", json={"enabled": True}).status_code == 200
        invalid = client.post("/api/v1/alert-rules", json={
            "parameter": "co2", "operator": "gt", "threshold": 1000, "unit": "ppb",
            "severity": "warning", "message": "bad unit", "recommended_action": "Check",
        })
        assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "validation_error"
        bad_page = client.get("/api/v1/alerts?limit=500")
        assert bad_page.status_code == 422
        assert bad_page.json()["detail"]["code"] == "validation_error"

        with store() as session:
            with session.begin():
                session.add(Device(id="device-1", name="sensor"))
        row_id = add_reading(store, datetime.now(timezone.utc), co2=1200)
        evaluate_reading(store, row_id)
        device_response = client.get("/api/v1/devices/device-1/status")
        assert device_response.status_code == 200 and device_response.json()["status"] == "never_seen"
        assert device_response.json()["latest_reading"]["co2"] == 1200
        with store() as session:
            with session.begin():
                session.add(Device(id="device-2", name="sensor two"))
        row_two = add_reading(store, datetime.now(timezone.utc), device="device-2", co2=1300, entry=2)
        evaluate_reading(store, row_two)
        page = client.get("/api/v1/alerts?status=active&offset=0&limit=1")
        assert page.status_code == 200 and page.json()["total"] == 2 and len(page.json()["items"]) == 1
        assert client.get("/api/v1/alerts?status=active&offset=1&limit=1").json()["total"] == 2
        alert_id = page.json()["items"][0]["id"]
        assert client.patch(f"/api/v1/alert-rules/{created.json()['id']}", json={"threshold": 2000}).status_code == 409
        ack = client.post(f"/api/v1/alerts/{alert_id}/acknowledge", json={"actor": "operator"})
        assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
        assert client.get("/api/v1/alerts/active").json()["total"] == 2
        assert client.post(f"/api/v1/alerts/{alert_id}/resolve", json={}).json()["status"] == "resolved"
        assert client.get("/api/v1/alerts/active").json()["total"] == 1
        notifications = client.get("/api/v1/notifications/in-app")
        assert notifications.status_code == 200 and notifications.json()["total"] == 2
    app.dependency_overrides.clear()


def test_production_alert_mutations_require_bearer_token(store, monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: settings(app_env="production"))
    app = create_app(app_settings=settings(app_env="test"))
    def no_db():
        yield None
    app.dependency_overrides[get_db] = no_db
    with TestClient(app) as client:
        response = client.post("/api/v1/alert-rules", json={
            "parameter": "voc", "operator": "gt", "threshold": 10, "unit": "sensor-index",
            "severity": "warning", "message": "VOC high", "recommended_action": "Check ventilation",
        })
    app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "admin_auth_not_configured"


def test_configured_admin_token_protects_mutations(store, monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: settings(app_env="production", alerts_admin_token="test-admin-token"))
    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    payload = {
        "parameter": "voc", "operator": "gt", "threshold": 10, "unit": "sensor-index",
        "severity": "warning", "message": "VOC high", "recommended_action": "Check ventilation",
    }
    with TestClient(app) as client:
        assert client.post("/api/v1/alert-rules", json=payload).status_code == 401
        created = client.post("/api/v1/alert-rules", json=payload, headers={"Authorization": "Bearer test-admin-token"})
        assert created.status_code == 201
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ingestion_loop_stops_cleanly():
    now = datetime.now(timezone.utc)
    client = FakeClient([[]])
    service = ThingSpeakIngestionService(settings(), None, client, clock=lambda: now)
    calls = 0
    reached = asyncio.Event()

    async def one_poll():
        nonlocal calls
        calls += 1
        reached.set()

    service.poll_once = one_poll
    stop_event = asyncio.Event()
    task = asyncio.create_task(service.run(stop_event))
    await asyncio.wait_for(reached.wait(), timeout=2)
    stop_event.set()
    await asyncio.wait_for(task, timeout=2)
    assert calls == 1


def test_app_lifespan_skips_ingestion_in_test_environment(store):
    created = []
    app = create_app(
        app_settings=settings(app_env="test", ingestion_enabled=True),
        session_factory=store,
        client_factory=lambda *args: created.append(args),
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").status_code == 200
    assert created == []


def test_app_lifespan_starts_and_stops_ingestion(store):
    polled = threading.Event()

    class ObservableClient(FakeClient):
        async def recent(self, count):
            polled.set()
            return []

    app = create_app(
        app_settings=settings(app_env="development", ingestion_enabled=True, thingspeak_poll_interval_seconds=15),
        session_factory=store,
        client_factory=lambda *args: ObservableClient([]),
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert polled.wait(timeout=3)
    with store() as session:
        device = session.get(Device, "thingspeak-3509505")
    assert device is not None and device.last_successful_ingestion_at is not None


def test_persisted_history_analytics_trends_and_time_validation(store):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=2)
    end = now + timedelta(hours=1)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
            make_rule(session, threshold=500)
    first = add_reading(store, start + timedelta(minutes=5), co2=400, voc=20, entry=1)
    second = add_reading(store, start + timedelta(minutes=65), co2=600, voc=40, entry=2)
    evaluate_reading(store, first)
    evaluate_reading(store, second)

    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    params = {"start_at": start.isoformat(), "end_at": end.isoformat(), "device_id": "device-1"}
    with TestClient(app) as client:
        history = client.get("/api/v1/readings/history", params={**params, "limit": 1, "offset": 1})
        assert history.status_code == 200
        assert history.json()["total"] == 2 and history.json()["items"][0]["id"] == second

        summary = client.get("/api/v1/analytics/summary", params=params)
        assert summary.status_code == 200
        body = summary.json()
        assert body["reading_count"] == 2
        assert body["metrics"]["co2"] == {"count": 2, "minimum": 400.0, "maximum": 600.0, "average": 500.0, "unit": "ppm"}
        assert body["metrics"]["pm25"]["count"] == 0 and body["metrics"]["pm25"]["average"] is None
        assert body["metrics"]["voc"]["unit"] == "sensor-index"
        assert body["alert_counts_by_severity"]["warning"] == 1
        assert body["alert_counts_by_parameter"]["co2"] == 1

        trend = client.get("/api/v1/analytics/trends", params={**params, "parameter": "co2", "points": 100})
        assert trend.status_code == 200
        assert [point["value"] for point in trend.json()["points"]] == [400.0, 600.0]
        assert sum(point["sample_count"] for point in trend.json()["points"]) == 2

        invalid = client.get("/api/v1/analytics/summary", params={"start_at": end.isoformat(), "end_at": start.isoformat()})
        assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "invalid_time_range"
    app.dependency_overrides.clear()


def test_historical_backfill_origin_is_exposed_in_reading_and_analytics_apis(store):
    now = datetime.now(timezone.utc)
    add_reading(store, now - timedelta(minutes=2), co2=500, entry=1, ingestion_mode="live")
    add_reading(store, now - timedelta(minutes=1), co2=700, entry=2, ingestion_mode="historical_backfill")
    app = create_app(app_settings=settings(app_env="test"))

    def override_db():
        with store() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        period = {"start_at": (now - timedelta(hours=1)).isoformat(), "end_at": (now + timedelta(minutes=1)).isoformat()}
        history = client.get("/api/v1/readings/history", params=period)
        assert [item["ingestion_mode"] for item in history.json()["items"]] == ["live", "historical_backfill"]

        summary = client.get("/api/v1/analytics/summary", params=period)
        assert summary.json()["ingestion_mode_counts"] == {"live": 1, "historical_backfill": 1}

        trend = client.get("/api/v1/analytics/trends", params={**period, "parameter": "co2"})
        assert trend.json()["ingestion_modes"] == ["historical_backfill", "live"]
    app.dependency_overrides.clear()


def test_analytics_exceedance_duration_counts_only_bounded_consecutive_samples(store, monkeypatch):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            make_rule(session, threshold=600)
            make_rule(session, id="voc-unit-rule", parameter="voc", threshold=50, unit="sensor-index")
    add_reading(store, now - timedelta(minutes=40), co2=710, entry=5, ingestion_mode="live")
    add_reading(store, now - timedelta(minutes=30), co2=None, entry=6, ingestion_mode="live")
    add_reading(store, now - timedelta(minutes=20), co2=810, entry=7, ingestion_mode="live")
    live_start = add_reading(store, now - timedelta(minutes=4), co2=700, entry=1, ingestion_mode="live")
    live_next = add_reading(store, now - timedelta(minutes=3), co2=800, entry=2, ingestion_mode="live")
    add_reading(store, now - timedelta(minutes=2), co2=500, entry=3, ingestion_mode="live")
    add_reading(store, now - timedelta(minutes=1), co2=700, entry=4, ingestion_mode="live")
    backfill_start = add_reading(store, now - timedelta(minutes=4), co2=710, entry=11, ingestion_mode="historical_backfill")
    backfill_next = add_reading(store, now - timedelta(minutes=2), co2=810, entry=12, ingestion_mode="historical_backfill")
    assert live_start and live_next and backfill_start and backfill_next
    voc_ids = [
        add_reading(store, now - timedelta(minutes=3), voc=80, entry=21),
        add_reading(store, now - timedelta(minutes=2), voc=90, entry=22),
        add_reading(store, now - timedelta(minutes=1), voc=100, entry=23),
    ]
    with store() as session:
        with session.begin():
            for reading_id in voc_ids[1:]:
                session.get(Reading, reading_id).voc_unit = "ppb"

    monkeypatch.setattr(api, "get_settings", lambda: settings(reading_stale_after_seconds=900))
    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        summary = client.get("/api/v1/analytics/summary", params={
            "start_at": (now - timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(minutes=1)).isoformat(),
        })
    app.dependency_overrides.clear()

    assert summary.status_code == 200
    durations = summary.json()["exceedance_durations"]
    assert {(row["ingestion_mode"], row["observed_seconds"], row["observed_intervals"]) for row in durations} == {
        ("live", 60, 1),
        ("historical_backfill", 120, 1),
    }
    assert all(row["parameter"] == "co2" for row in durations)


def test_voc_trend_refuses_mixed_units(store):
    now = datetime.now(timezone.utc)
    with store() as session:
        with session.begin():
            session.add(Device(id="device-1", name="sensor"))
    add_reading(store, now - timedelta(minutes=1), voc=20, entry=1)
    with store() as session:
        with session.begin():
            row = session.scalar(select(Reading))
            row.voc_unit = "ppb"
    add_reading(store, now, voc=30, entry=2)
    with store() as session:
        with session.begin():
            row = session.scalars(select(Reading).order_by(Reading.timestamp.desc())).first()
            row.voc_unit = "sensor-index"
    app = create_app(app_settings=settings(app_env="test"))
    def override_db():
        with store() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        response = client.get("/api/v1/analytics/trends", params={
            "start_at": (now - timedelta(hours=1)).isoformat(), "end_at": (now + timedelta(minutes=1)).isoformat(), "parameter": "voc",
        })
    app.dependency_overrides.clear()
    assert response.status_code == 409 and response.json()["detail"]["code"] == "mixed_voc_units"
