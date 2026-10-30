import httpx
import pytest
from datetime import datetime, timedelta, timezone

from app.thingspeak import ThingSpeakClient, ThingSpeakError, normalize_feed

GOOD_FEED = {"created_at": "2026-10-28T10:00:00Z", "entry_id": "12", "field1": "510", "field2": "8.2", "field3": "42", "field4": "23.5", "field5": "45", "field6": "ok", "field7": "3.7"}


def make_client(handler, *, key=None):
    return ThingSpeakClient(3509505, key, transport=httpx.MockTransport(handler))


def test_maps_fields_and_keeps_voc_unit_unknown_by_default():
    reading = normalize_feed(GOOD_FEED, 3509505)
    assert reading.device_id == "thingspeak-3509505"
    assert reading.timestamp.tzinfo == timezone.utc
    assert (reading.co2, reading.pm25, reading.voc, reading.voc_unit) == (510, 8.2, 42, None)
    assert (reading.temperature, reading.humidity, reading.device_status, reading.battery) == (23.5, 45, "ok", 3.7)


def test_missing_measurements_are_preserved_and_all_missing_are_rejected():
    reading = normalize_feed({"created_at": "2026-10-28T10:00:00Z", "field3": "10"}, 1, "sensor-index")
    assert reading.co2 is None and reading.pm25 is None and reading.voc_unit == "sensor-index"
    with pytest.raises(ValueError, match="no environmental measurements"):
        normalize_feed({"created_at": "2026-10-28T10:00:00Z"}, 1)


@pytest.mark.parametrize("field,value", [("field1", "nan"), ("field2", "-1"), ("field5", "101"), ("field4", "bad")])
def test_invalid_measurements_rejected(field, value):
    with pytest.raises(ValueError):
        normalize_feed({**GOOD_FEED, field: value}, 1)


def test_invalid_timestamp_rejected():
    with pytest.raises(ValueError, match="Invalid ThingSpeak feed"):
        normalize_feed({**GOOD_FEED, "created_at": "not-a-timestamp"}, 1)


def test_timestamp_offsets_are_converted_to_utc_without_changing_the_instant():
    offset = normalize_feed({**GOOD_FEED, "created_at": "2026-10-28T15:30:00+05:30"}, 1)
    utc = normalize_feed({**GOOD_FEED, "created_at": "2026-10-28T10:00:00Z"}, 1)
    assert offset.timestamp == utc.timestamp
    assert offset.timestamp.isoformat() == "2026-10-28T10:00:00+00:00"


def test_naive_provider_timestamp_is_explicitly_interpreted_as_utc():
    reading = normalize_feed({**GOOD_FEED, "created_at": "2026-10-28T10:00:00"}, 1)
    assert reading.timestamp == datetime(2026, 10, 28, 10, tzinfo=timezone.utc)


def test_future_timestamp_and_nonpositive_entry_id_rejected():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        normalize_feed({**GOOD_FEED, "created_at": future}, 1)
    with pytest.raises(ValueError, match="entry_id"):
        normalize_feed({**GOOD_FEED, "entry_id": "0"}, 1)


@pytest.mark.asyncio
async def test_empty_feed_is_valid_and_missing_key_is_not_sent():
    def handler(request):
        assert "api_key" not in request.url.params
        return httpx.Response(200, json={"feeds": []})

    assert await make_client(handler).latest() == []


@pytest.mark.asyncio
async def test_maps_mocked_upstream_response():
    client = make_client(lambda request: httpx.Response(200, json={"feeds": [GOOD_FEED]}))
    readings = await client.latest()
    assert len(readings) == 1 and readings[0].provider_entry_id == 12


@pytest.mark.parametrize("payload", [{}, {"feeds": None}, None])
@pytest.mark.asyncio
async def test_malformed_upstream_response_is_sanitized(payload):
    client = make_client(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ThingSpeakError, match="invalid response"):
        await client.latest()


@pytest.mark.asyncio
async def test_invalid_feed_records_are_skipped_and_valid_records_sorted():
    later = {**GOOD_FEED, "entry_id": "13", "created_at": "2026-10-28T10:01:00Z"}
    def handler(request):
        assert request.url.params["results"] == "10"
        return httpx.Response(200, json={"feeds": [later, "malformed", GOOD_FEED]})
    client = make_client(handler)
    readings = await client.recent(10)
    assert [reading.provider_entry_id for reading in readings] == [12, 13]


@pytest.mark.parametrize("exception", [httpx.ReadTimeout("secret timeout details"), httpx.ConnectError("secret network details")])
@pytest.mark.asyncio
async def test_timeout_and_transport_errors_are_sanitized(exception):
    def handler(request):
        raise exception

    with pytest.raises(ThingSpeakError) as exc_info:
        await make_client(handler, key="secret-api-key").latest()
    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize("status", [401, 429, 500])
@pytest.mark.asyncio
async def test_http_errors_are_sanitized(status):
    client = make_client(lambda request: httpx.Response(status, text="provider secret details"), key="secret-api-key")
    with pytest.raises(ThingSpeakError, match="ThingSpeak is unavailable") as exc_info:
        await client.latest()
    assert "secret" not in str(exc_info.value)
