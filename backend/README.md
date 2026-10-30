# AirShield backend

FastAPI and SQLAlchemy backend for ThingSpeak ingestion, device freshness, configurable operational alert rules, alert lifecycle management, in-app notification records, and persisted historical analytics. The repository root README documents the connected React application setup.

## Windows PowerShell setup

From the `backend` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `DATABASE_URL` to a reachable PostgreSQL database. `THINGSPEAK_READ_API_KEY` is optional for public channel 3509505. Never put credentials in frontend code or commit `.env`. Leave `THINGSPEAK_VOC_UNIT` empty until the actual VOC sensor output semantics are confirmed. Set `THINGSPEAK_DEVICE_ID`, name, and location to stable deployment metadata if desired.

Run migrations and start the API:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

The `.env.example` enables the background ingestion loop. The loop starts only with the FastAPI lifespan and stops at shutdown. `APP_ENV=test` always disables it. `INGESTION_ENABLED=false` disables it in development/production. The default interval is 30 seconds, and configuration rejects intervals below 15 seconds; batch size defaults to 100 feeds. Request timeouts default to 8 seconds and are capped at 60 seconds. Run one process per database/channel for this in-process scheduler; each polling loop is single-instance within its application process.

Open API documentation at `http://127.0.0.1:8000/docs`.

## Ingestion and device status

Each successful poll requests the configured recent batch, normalizes valid feeds, sorts them chronologically, and writes new rows transactionally. Individual malformed feeds are skipped and logged by batch position and error type. Feeds older than `READING_STALE_AFTER_SECONDS` (default 900 seconds) are not inserted by live polling; their timestamps can still update device `last_seen_at` to show the stale feed age. Empty successful responses update last-successful-ingestion time but do not fabricate readings. ThingSpeak errors are logged without credentials and do not stop later polls.

`GET /api/v1/readings/latest` reads the newest persisted sample and computes freshness using the backend's configured cutoff. It does not make an independent ThingSpeak request. This keeps dashboard values aligned with deduplication and alert evaluation and lets historical persisted data remain visible with its origin and stale state during an upstream outage.

### Explicit historical backfill

Historical valid feeds can be imported once for analytics after applying migrations (`alembic upgrade head`):

```powershell
python -m app.backfill --limit 8000
```

The command requests up to the most recent 8,000 feeds supported by this client, preserves each provider timestamp, and is safe to rerun because provider feed identity is unique. These rows are stored with `ingestion_mode=historical_backfill`; they are not evaluated for alerts. Historical readings remain stale in the live/latest and device-status views when their original timestamps exceed the freshness window. Historical data is available to analytics, which reports the source mode. Backfill does not change the freshness cutoff or create synthetic readings. The normal scheduler remains live-only and continues to skip stale feeds.

Deduplication uses `(device_id, provider_entry_key)`. ThingSpeak entry IDs form the normal key; feeds without an entry ID use a stable SHA-256 key derived from timestamp and measurements. The migration backfills keys for existing rows that have an entry ID. Persisted readings are committed before alert evaluation. Pending evaluations are retried on the next polling cycle, so evaluator failure does not discard the reading.

Device status uses only the newest `live`-origin reading (never a historical backfill row). Let F be `READING_STALE_AFTER_SECONDS`: `online` is age ≤ F, `stale` is F < age ≤ 2F, and `offline` is age > 2F. A device with no live feed is `never_seen`. A ThingSpeak poll failure newer than the last successful poll makes status `unknown`; the failure does not itself create, advance, or resolve a health alert. On the next successful poll, the persisted live-feed age is evaluated again. Each transition to stale/offline creates one persisted `device_health` alert; transition to online resolves any unresolved health alert. `health_state` is stored on the device, so repeated polls and service restarts do not duplicate a transition. The feed timestamp is only a heartbeat proxy and cannot prove the physical sensor is powered or calibrated. Migration `0004_device_health_alerts` adds this representation without deleting existing environmental alerts.

## Alert rules and lifecycle

Rules are created through `POST /api/v1/alert-rules` and listed/updated under `/api/v1/alert-rules`. Supported parameters are CO2 (`ppm`), PM2.5 (`µg/m³`), VOC (an explicitly supplied sensor unit), temperature (`°C`), and humidity (`%RH`). Operators are `gt`, `gte`, `lt`, and `lte`; severities are `info`, `warning`, and `critical`. A positive persistence duration requires at least two abnormal readings spanning that duration. If the gap between abnormal samples exceeds the freshness window, persistence starts again. Missing parameters are ignored.

High-threshold rules recover at or below the configured recovery threshold; low-threshold rules recover at or above it. If no recovery threshold is set, the trigger boundary is used with the inverse comparison. There is at most one unresolved alert per device/rule. Alert states are `active`, `acknowledged`, and `resolved`; automatic recovery resolves either active or acknowledged alerts. Alerts store the trigger value, threshold/unit, severity, recommended action, occurrence time, condition-start time, acknowledgement, and resolution details.

No default alert thresholds are seeded. Threshold values are operational project settings and require validation against appropriate indoor guidance, averaging windows, and actual sensor behavior. The API does not claim that a configured threshold is a universal health or ambient-air regulatory limit.

## Alerts, devices, and notifications API

- `GET /api/v1/alerts` supports `status`, `severity`, `parameter` (including `device_health`), `device_id`, `offset`, and `limit` filters.
- `GET /api/v1/alerts/active` lists active and acknowledged alerts; `GET /api/v1/alerts/{id}` reads detail.
- `POST /api/v1/alerts/{id}/acknowledge` and `/resolve` change alert state.
- `GET /api/v1/devices` and `/api/v1/devices/{id}/status` report device identity, freshness, last-seen time, last successful poll, latest live reading, most recent provider poll failure, and any unresolved `health_alert` transition.
- `GET /api/v1/notifications/in-app` lists persisted in-app alert notifications. Their status is `recorded`, which means the record is available in the API, not that a browser/email was notified.
- `GET /api/v1/readings/history` returns persisted readings for a validated time interval with pagination.
- `GET /api/v1/analytics/summary` calculates per-parameter count, minimum, maximum, and average plus alert counts from persisted data. For enabled rules it also returns conservative observed exceedance duration, counting only intervals between consecutive abnormal samples within the freshness window. Missing/gapped periods and time before the first or after the last observation are omitted; this is not continuous exposure measurement.
- `GET /api/v1/analytics/trends` returns time-binned persisted averages. Missing periods are omitted; mixed/unknown VOC units return HTTP 409 instead of combining incompatible values.

Notification adapters expose browser and email delivery interfaces. No provider is configured by default. When an adapter is explicitly supplied, the service records `pending`, attempt time, and then `delivered` only after the adapter confirms by returning; failures store only the exception type and can be retried. No browser/email notification is automatically claimed or attempted without a configured adapter.

Administrative mutation routes accept a bearer token set with `ALERTS_ADMIN_TOKEN`. If `APP_ENV=production` and no token is configured, mutations return 503; if a token is configured, requests must include `Authorization: Bearer <token>`. Development mode permits mutations without a token for local use. This is a single shared administrative token, not user identity or a complete production security system; deploy behind TLS and add appropriate identity, audit, and access controls before production use.

## Checks and database notes

```powershell
python -m compileall -q app migrations tests
python -m pytest -q
alembic upgrade head
```

Tests mock ThingSpeak HTTP and use isolated SQLite files. SQLite drops timezone metadata when reading `DateTime(timezone=True)` values and differs from PostgreSQL in locking, partial indexes, and constraint behavior. Migration 0003 adds the ingestion-origin marker with existing rows defaulted to `live`; it does not replace or delete data. PostgreSQL behavior should be verified against the configured database before deployment.
