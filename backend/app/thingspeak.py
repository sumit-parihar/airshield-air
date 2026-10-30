import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.schemas import AirQualityReading

logger = logging.getLogger(__name__)
# HTTPX logs full request URLs at INFO; ThingSpeak read keys are query params.
# Apply this in the integration module so CLI and API entry points are both safe.
logging.getLogger("httpx").setLevel(logging.WARNING)


class ThingSpeakError(Exception):
    """Safe, user-facing upstream integration error."""


def _number(value: Any, field: str, *, minimum: float | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{field} is outside the valid range")
    return result


def normalize_feed(feed: dict[str, Any], channel_id: int, voc_unit: str | None = None) -> AirQualityReading:
    try:
        timestamp = datetime.fromisoformat(str(feed["created_at"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        if timestamp > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("created_at cannot be in the future")
        entry_id = int(feed["entry_id"]) if feed.get("entry_id") not in (None, "") else None
        if entry_id is not None and entry_id < 1:
            raise ValueError("entry_id must be positive")
        values = {
            "co2": _number(feed.get("field1"), "field1", minimum=0),
            "pm25": _number(feed.get("field2"), "field2", minimum=0),
            "voc": _number(feed.get("field3"), "field3", minimum=0),
            "temperature": _number(feed.get("field4"), "field4"),
            "humidity": _number(feed.get("field5"), "field5", minimum=0),
            "battery": _number(feed.get("field7"), "field7", minimum=0),
        }
        if values["humidity"] is not None and values["humidity"] > 100:
            raise ValueError("field5 is outside the valid range")
        if not any(values[key] is not None for key in ("co2", "pm25", "voc", "temperature", "humidity")):
            raise ValueError("feed has no environmental measurements")
        return AirQualityReading(
            provider_entry_id=entry_id,
            device_id=f"thingspeak-{channel_id}",
            timestamp=timestamp,
            **values,
            voc_unit=voc_unit,
            device_status=str(feed["field6"]) if feed.get("field6") not in (None, "") else None,
            source_metadata={"channel_id": channel_id},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ThingSpeak feed: {exc}") from exc


class ThingSpeakClient:
    def __init__(
        self,
        channel_id: int,
        read_api_key: str | None,
        timeout: float = 8,
        voc_unit: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.channel_id = channel_id
        self.read_api_key = read_api_key
        self.timeout = timeout
        self.voc_unit = voc_unit
        self.transport = transport
        self.url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"

    async def latest(self) -> list[AirQualityReading]:
        return await self.recent(1)

    async def recent(self, results: int = 100) -> list[AirQualityReading]:
        if not 1 <= results <= 8000:
            raise ValueError("results must be between 1 and 8000")
        params = {"results": results}
        if self.read_api_key:
            params["api_key"] = self.read_api_key
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(self.url, params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("ThingSpeak request failed (%s)", type(exc).__name__)
            raise ThingSpeakError("ThingSpeak is unavailable") from exc
        try:
            payload = response.json()
            feeds = payload["feeds"]
            if not isinstance(feeds, list):
                raise ValueError("feeds must be a list")
            readings = []
            for index, item in enumerate(feeds):
                try:
                    if not isinstance(item, dict):
                        raise ValueError("feed entry must be an object")
                    readings.append(normalize_feed(item, self.channel_id, self.voc_unit))
                except ValueError as exc:
                    logger.warning("Skipping invalid ThingSpeak feed at batch index %s (%s)", index, type(exc).__name__)
            readings.sort(key=lambda reading: reading.timestamp)
            return readings
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("ThingSpeak response validation failed (%s)", type(exc).__name__)
            raise ThingSpeakError("ThingSpeak returned an invalid response") from exc
