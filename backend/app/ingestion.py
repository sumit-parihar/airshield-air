"""Single-process, lifecycle-managed ThingSpeak polling and persistence."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.alert_engine import evaluate_reading
from app.config import Settings
from app.models import Alert, Device, NotificationRecord, Reading
from app.notifications import NotificationService
from app.schemas import AirQualityReading
from app.thingspeak import ThingSpeakClient, ThingSpeakError

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def provider_entry_key(reading: AirQualityReading, channel_id: int) -> str:
    """Stable identity, including a content-derived key for feeds without entry_id."""
    if reading.provider_entry_id is not None:
        return f"{reading.device_id}:{reading.provider_entry_id}"
    canonical = {
        "timestamp": _utc(reading.timestamp).isoformat(),
        "co2": reading.co2,
        "pm25": reading.pm25,
        "voc": reading.voc,
        "temperature": reading.temperature,
        "humidity": reading.humidity,
        "device_status": reading.device_status,
        "battery": reading.battery,
    }
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"{channel_id}:sha256:{digest}"


@dataclass(frozen=True)
class IngestionResult:
    request_succeeded: bool
    received: int = 0
    inserted: int = 0
    duplicates: int = 0
    stale_skipped: int = 0
    evaluation_failures: int = 0


class ThingSpeakIngestionService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        client: ThingSpeakClient | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        evaluator: Callable[..., int] = evaluate_reading,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.client = client or ThingSpeakClient(
            settings.thingspeak_channel_id,
            settings.thingspeak_read_api_key,
            settings.thingspeak_timeout_seconds,
            settings.thingspeak_voc_unit,
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.evaluator = evaluator
        self.device_id = settings.thingspeak_device_id or f"thingspeak-{settings.thingspeak_channel_id}"

    async def poll_once(self) -> IngestionResult:
        try:
            feeds = await self.client.recent(self.settings.thingspeak_batch_size)
        except ThingSpeakError as exc:
            logger.warning("ThingSpeak ingestion request failed (%s)", type(exc.__cause__ or exc).__name__)
            self._record_poll_failure()
            return IngestionResult(request_succeeded=False)

        return self._persist_feeds(feeds, ingestion_mode="live", skip_stale=True, evaluate=True)

    async def backfill_history_once(self, results: int = 8000) -> IngestionResult:
        """Persist valid historical feeds without making them eligible for live alerts."""
        if not 1 <= results <= 8000:
            raise ValueError("results must be between 1 and 8000")
        try:
            feeds = await self.client.recent(results)
        except ThingSpeakError as exc:
            logger.warning("ThingSpeak historical backfill request failed (%s)", type(exc.__cause__ or exc).__name__)
            return IngestionResult(request_succeeded=False)
        return self._persist_feeds(feeds, ingestion_mode="historical_backfill", skip_stale=False, evaluate=False)

    def _persist_feeds(
        self,
        feeds: list[AirQualityReading],
        *,
        ingestion_mode: str,
        skip_stale: bool,
        evaluate: bool,
    ) -> IngestionResult:
        if ingestion_mode not in ("live", "historical_backfill"):
            raise ValueError("Unsupported ingestion mode")

        feeds = sorted(feeds, key=lambda reading: _utc(reading.timestamp))
        now = _utc(self.clock())
        cutoff = now - timedelta(seconds=self.settings.reading_stale_after_seconds)
        if skip_stale:
            accepted_feeds = [reading for reading in feeds if _utc(reading.timestamp) >= cutoff]
            stale_count = len(feeds) - len(accepted_feeds)
        else:
            accepted_feeds = feeds
            stale_count = 0
        new_ids: list[int] = []
        duplicate_count = 0

        with self.session_factory() as session:
            with session.begin():
                device = session.get(Device, self.device_id)
                if device is None:
                    device = Device(
                        id=self.device_id,
                        name=self.settings.thingspeak_device_name,
                        location=self.settings.thingspeak_device_location,
                    )
                    session.add(device)
                    session.flush()
                else:
                    device.name = self.settings.thingspeak_device_name
                    device.location = self.settings.thingspeak_device_location

                for item in accepted_feeds:
                    key = provider_entry_key(item, self.settings.thingspeak_channel_id)
                    exists = session.scalar(
                        select(Reading.id).where(
                            Reading.device_id == self.device_id,
                            Reading.provider_entry_key == key,
                        )
                    )
                    if exists is not None:
                        duplicate_count += 1
                        continue
                    normalized = item.model_copy(update={"device_id": self.device_id})
                    row = Reading(
                        provider_entry_id=normalized.provider_entry_id,
                        provider_entry_key=key,
                        device_id=normalized.device_id,
                        timestamp=_utc(normalized.timestamp),
                        co2=normalized.co2,
                        pm25=normalized.pm25,
                        voc=normalized.voc,
                        voc_unit=normalized.voc_unit,
                        temperature=normalized.temperature,
                        humidity=normalized.humidity,
                        device_status=normalized.device_status,
                        battery=normalized.battery,
                        source=normalized.source,
                        source_metadata=normalized.source_metadata,
                        ingestion_mode=ingestion_mode,
                    )
                    try:
                        with session.begin_nested():
                            session.add(row)
                            session.flush()
                        new_ids.append(row.id)
                    except IntegrityError:
                        duplicate_count += 1

                # A historical import is not evidence that the live polling loop
                # recently contacted the provider; keep device health tied to
                # scheduled ingestion only.
                if ingestion_mode == "live":
                    device.last_successful_ingestion_at = now
                    device.last_ingestion_error_at = None
                    latest_feed_time = max((_utc(item.timestamp) for item in feeds), default=None)
                    if latest_feed_time is not None and (device.last_seen_at is None or latest_feed_time > _utc(device.last_seen_at)):
                        device.last_seen_at = latest_feed_time
                    self._evaluate_device_health(session, device, now)

        failures = self._evaluate_pending() if evaluate else 0
        result = IngestionResult(
            request_succeeded=True,
            received=len(feeds),
            inserted=len(new_ids),
            duplicates=duplicate_count,
            stale_skipped=stale_count,
            evaluation_failures=failures,
        )
        logger.info(
            "ThingSpeak ingestion completed received=%d inserted=%d duplicates=%d stale_skipped=%d evaluation_failures=%d",
            result.received,
            result.inserted,
            result.duplicates,
            result.stale_skipped,
            result.evaluation_failures,
        )
        return result

    def _record_poll_failure(self) -> None:
        """Record provider outages for honest status; outages do not alter alert state."""
        try:
            with self.session_factory() as session:
                with session.begin():
                    device = session.get(Device, self.device_id)
                    failed_at = _utc(self.clock())
                    if device is None:
                        device = Device(
                            id=self.device_id,
                            name=self.settings.thingspeak_device_name,
                            location=self.settings.thingspeak_device_location,
                            last_ingestion_error_at=failed_at,
                        )
                        session.add(device)
                    else:
                        device.last_ingestion_error_at = failed_at
        except Exception as exc:
            logger.error("Could not persist ThingSpeak poll failure (%s)", type(exc).__name__)

    def _evaluate_device_health(self, session: Session, device: Device, now: datetime) -> None:
        """Apply transition alerts only after a successful live provider poll.

        Stale means age > F; offline means age > 2F. Backfill never calls this.
        """
        latest = session.scalar(
            select(Reading).where(Reading.device_id == device.id, Reading.ingestion_mode == "live")
            .order_by(Reading.timestamp.desc(), Reading.id.desc()).limit(1)
        )
        if latest is None:
            next_state = "never_seen"
        else:
            age = (now - _utc(latest.timestamp)).total_seconds()
            freshness = self.settings.reading_stale_after_seconds
            next_state = "online" if age <= freshness else "stale" if age <= freshness * 2 else "offline"

        if device.health_state == next_state:
            return
        previous_state = device.health_state
        device.health_state = next_state
        device.health_state_since = now
        unresolved = session.query(Alert).filter(
            Alert.device_id == device.id,
            Alert.category == "device_health",
            Alert.status.in_(("active", "acknowledged")),
        ).all()
        for alert in unresolved:
            alert.status = "resolved"
            alert.resolved_at = now
            alert.resolution = "condition_recovered" if next_state == "online" else "health_state_changed"

        if next_state not in ("stale", "offline"):
            return
        # Alert is a state transition record, not a fabricated sensor sample.
        boundary = 1 if next_state == "stale" else 2
        condition_started = _utc(latest.timestamp) + timedelta(seconds=self.settings.reading_stale_after_seconds * boundary)
        alert = Alert(
            device_id=device.id,
            rule_id=None,
            reading_id=None,
            category="device_health",
            health_state=next_state,
            status="active",
            parameter="device_health",
            observed_value=None,
            threshold=None,
            unit="state",
            severity="warning" if next_state == "stale" else "critical",
            message=f"Device {device.name} is {next_state}",
            recommended_action=(
                "Check the device and its data connection; the latest feed has exceeded the configured freshness window."
                if next_state == "stale" else
                "Check device power, network connectivity, and ThingSpeak publishing; no fresh feed arrived within two freshness windows."
            ),
            created_at=now,
            condition_started_at=condition_started,
        )
        session.add(alert)
        session.flush()
        NotificationService.record_in_app(session, alert)
        logger.info("Device health transition device_id=%s from=%s to=%s", device.id, previous_state or "uninitialized", next_state)

    def _evaluate_pending(self) -> int:
        with self.session_factory() as session:
            pending_ids = list(
                session.scalars(
                    select(Reading.id)
                    .where(Reading.alerts_evaluated_at.is_(None))
                    .where(Reading.ingestion_mode == "live")
                    .order_by(Reading.timestamp, Reading.id)
                    .limit(self.settings.thingspeak_batch_size)
                )
            )

        failures = 0
        for reading_id in pending_ids:
            try:
                self.evaluator(
                    self.session_factory,
                    reading_id,
                    freshness_seconds=self.settings.reading_stale_after_seconds,
                    evaluated_at=_utc(self.clock()),
                )
            except Exception as exc:
                failures += 1
                with self.session_factory() as session:
                    with session.begin():
                        row = session.get(Reading, reading_id)
                        if row is not None:
                            row.alert_evaluation_error = type(exc).__name__[:160]
                logger.error("Alert evaluation failed for persisted reading id=%s (%s)", reading_id, type(exc).__name__)
                continue
            with self.session_factory() as session:
                with session.begin():
                    row = session.get(Reading, reading_id)
                    if row is not None:
                        row.alerts_evaluated_at = _utc(self.clock())
                        row.alert_evaluation_error = None
        return failures

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info("ThingSpeak ingestion loop started interval_seconds=%d", self.settings.thingspeak_poll_interval_seconds)
        loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            started = loop.time()
            try:
                await self.poll_once()
            except Exception as exc:
                logger.error("ThingSpeak ingestion cycle failed (%s)", type(exc).__name__)
            wait_seconds = max(0.0, self.settings.thingspeak_poll_interval_seconds - (loop.time() - started))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            except TimeoutError:
                continue
        logger.info("ThingSpeak ingestion loop stopped")
