# AirShield operational runbook

This runbook covers the existing React/Vite and FastAPI application. Sensor values shown as live must have been received from ThingSpeak and persisted by backend ingestion. Historical backfill remains useful for analytics but cannot establish current device health.

## Configuration names

Backend local file: `backend/.env` (copy `backend/.env.example` only if the file does not already exist). Required or relevant names:

- `APP_ENV`
- `DATABASE_URL`
- `CORS_ORIGINS`
- `THINGSPEAK_CHANNEL_ID`
- `THINGSPEAK_READ_API_KEY` (only if the channel requires a read key)
- `THINGSPEAK_WRITE_API_KEY` (producer-side use; keep out of the browser)
- `THINGSPEAK_VOC_UNIT` (leave unset until confirmed from the actual VOC sensor)
- `THINGSPEAK_TIMEOUT_SECONDS`
- `THINGSPEAK_BATCH_SIZE`
- `THINGSPEAK_POLL_INTERVAL_SECONDS`
- `THINGSPEAK_DEVICE_ID`, `THINGSPEAK_DEVICE_NAME`, `THINGSPEAK_DEVICE_LOCATION`
- `INGESTION_ENABLED`
- `READING_STALE_AFTER_SECONDS`
- `ALERTS_ADMIN_TOKEN` (required for mutations in production mode)

Frontend local file: root `.env.local` (copy root `.env.example` only if absent):

- `VITE_API_BASE_URL`
- `VITE_POLL_INTERVAL_MS`

Never copy a backend secret into `.env.local` or a Vite `VITE_*` variable. Keep both local environment files private. The repository's current ignore rules exclude them.

## Start both services (Windows PowerShell)

Open two PowerShell terminals at the repository root. Do not overwrite existing `.env` or `.env.local` files.

Backend terminal:

```powershell
Set-Location C:\Users\disea\Documents\Academics\Projects\AirQualityMonitoring\backend
if (-not (Test-Path .\.env)) { Copy-Item .\.env.example .\.env }
if (-not (Test-Path .\.venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend terminal:

```powershell
Set-Location C:\Users\disea\Documents\Academics\Projects\AirQualityMonitoring
if (-not (Test-Path .\.env.local)) { Copy-Item .\.env.example .\.env.local }+npm install
npm run dev
```

Open the Vite address printed in its terminal (normally `http://localhost:5173`). If the backend environment has `ALERTS_ADMIN_TOKEN`, enter that token through the Settings page when performing rule or alert actions. The UI keeps the entered token in memory only; local development without a token is allowed unless the backend is in production mode.

## Health and read-only checks

In a third terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/readings/latest
Invoke-RestMethod http://127.0.0.1:8000/api/v1/devices
```

Liveness only confirms the API process responds. Readiness runs a database query. Latest returns the newest persisted sample, with `stale` computed using `READING_STALE_AFTER_SECONDS`; empty data returns an empty stale response. Device health uses the newest `live`-origin feed only. Historical samples retain original timestamps and `historical_backfill` origin and do not refresh device health or alerts.

ThingSpeak field mapping:

| Field | AirShield parameter | Units |
|---|---|---|
| field1 | CO₂ | ppm |
| field2 | PM2.5 | µg/m³ |
| field3 | VOC | Confirm actual installed sensor semantics; unset until verified |
| field4 | Temperature | °C |
| field5 | Relative humidity | %RH |
| field6 | Device status | Optional text |
| field7 | Battery/power | Optional, documented by producer |

The browser talks only to the AirShield API. The ingestion loop runs in the FastAPI lifespan, polls at the configured interval (minimum 15 seconds), and stops on application shutdown. Use a single backend process per channel/device for this in-process scheduler. After a successful poll, the newest live feed is `online` through age F, `stale` after F through age 2F, and `offline` after 2F, where F is `READING_STALE_AFTER_SECONDS`. A poll failure reports `unknown` while it is newer than the last successful poll; it does not create, advance, or resolve a device-health alert. The next successful poll resumes age evaluation. Stale/offline alerts are created only on persisted state transitions, and a fresh live feed resolves them.

## ESP32 setup and hardware placeholders

See [`firmware/esp32/README.md`](firmware/esp32/README.md) and `airshield_esp32.ino`. The scaffold has no chosen board, sensor drivers, pin assignments, or calibration constants. Its sensor adapter returns no data until implemented, so it cannot fabricate readings. Tomorrow, identify the exact ESP32 board and sensors, interfaces and pins, voltage requirements, libraries, warm-up, calibration and units. Create local `firmware/esp32/secrets.h` from the example and configure Wi-Fi plus the ThingSpeak write key. Add and verify the HTTPS root CA before publishing; TLS verification must remain enabled. Keep VOC field3 disabled until its units are known.

## Tomorrow: end-to-end genuine-reading checklist

1. Confirm PostgreSQL is running, `health/ready` reports ready, and a current backup has been created and verified (procedure below). Do not reset the database.
2. Fill the backend `.env` values locally, confirm channel ID 3509505, poll interval, freshness window, device identity, and any required read key. Never paste credentials into chat or the frontend.
3. Assemble the identified board/sensors, wire only from their actual datasheets, implement the adapter, and calibrate. Verify each sensor's units and validity before publishing. Leave VOC unconfigured if semantics are still unknown.
4. Compile and flash the firmware. Confirm Wi-Fi connection and a successful ThingSpeak update acknowledgement (positive entry ID); logs must not show the write key.
5. Wait at least one backend poll interval, then call `/api/v1/readings/latest`. Confirm the new provider timestamp is recent, `stale` is false, `ingestion_mode` is `live`, values/units match the sensor, and the device status is online. Confirm the same provider entry is not inserted a second time on another poll.
6. Inspect the Overview and Live Monitoring pages. The displayed timestamp should match the persisted API response and status should be fresh. Historical Analytics should count live and backfill origins separately and mark charts containing backfill.
7. Review alert rules and units in Settings. Use a documented, safe operational rule and real observed readings; do not force unsafe conditions or describe project thresholds as universal health/regulatory limits. Verify a genuine rule crossing, single unresolved alert deduplication, acknowledgement, and subsequent recovery if naturally safe and observable.
8. Confirm Settings labels in-app notifications as recorded and browser/email as not configured unless a real adapter confirms delivery. Save the timestamp, ThingSpeak entry ID, API response status (without credentials), and any calibration notes for the review.

## Troubleshooting

- **Stale data:** compare provider timestamp with UTC and the configured freshness window. A successful poll of an old feed does not make the measurement fresh. Wait for a new sensor upload; do not lower the cutoff just to clear the stale label.
- **ThingSpeak unavailable:** inspect backend logs for request failure type and poll recovery. Check channel access and local read configuration without printing keys. Persisted historical data may still appear but remains stale.
- **Database unavailable:** `health/ready` returns HTTP 503. Check PostgreSQL process/network, the private `DATABASE_URL`, and that Alembic is at head. Do not reset or recreate volumes to troubleshoot.
- **No readings:** verify ingestion is enabled outside test mode, API and database health, channel/field mapping and poll interval. Malformed or stale feeds are skipped for live ingestion. Only a genuine newly published feed verifies the complete live path.
- **No alerts:** check that a rule is enabled, its parameter/unit matches the sensor, the condition crosses its threshold for the configured persistence, and enough qualifying live readings exist. Backfilled history intentionally does not generate alerts.
- **Rule/API mutation denied:** in production mode configure `ALERTS_ADMIN_TOKEN` locally and provide it in Settings. A shared token is not a complete production identity system.
- **VOC:** never infer units from a field name. Confirm the sensor datasheet/library output and convert only with documented semantics.

## PostgreSQL backup procedure

No recent backup has been verified for this workspace. Before hardware testing or any deployment change, create and validate a custom-format dump. Keep the PostgreSQL password in the local PostgreSQL password file (`%APPDATA%\postgresql\pgpass.conf`) with user-only Windows ACLs; never place a password in the command line or terminal output. Create/edit that file locally with a secure text editor, add a matching `host:port:database:username:password` entry (escaping any colon or backslash), then restrict its ACL. Extract connection fields privately from the existing backend `DATABASE_URL` without printing that value. Ensure the `pg_dump` and `pg_restore` client major versions are compatible with the server.

```powershell
$backupDir = Join-Path $PWD 'backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$pgPassFile = Join-Path $env:APPDATA 'postgresql\pgpass.conf'
if (-not (Test-Path $pgPassFile)) { throw 'Create the local PostgreSQL password file first; do not put the password in this command.' }
icacls $pgPassFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
$backupFile = Join-Path $backupDir ("airshield-{0}.dump" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$dbHost = Read-Host 'Database host (from the private backend DATABASE_URL)'
$dbPort = Read-Host 'Database port'
$dbUser = Read-Host 'Database username'
$dbName = Read-Host 'Database name'
pg_dump --format=custom --no-owner --host $dbHost --port $dbPort --username $dbUser --dbname $dbName --file $backupFile
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed; keep the database unchanged and resolve the backup error.' }
pg_restore --list $backupFile | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Backup archive validation failed.' }
Get-Item $backupFile | Select-Object Name, Length, LastWriteTimeUtc
```

Replace the four connection placeholders only in the command entered locally. Do not restore over the active database as a verification step. Store a second protected copy on separate storage; a dump on the same disk is not a disaster-recovery copy.

## Limits and notification status

There is no verified recent backup, genuinely fresh sensor reading, calibrated physical sensor, confirmed VOC unit, or board-specific firmware build in this workspace. PostgreSQL was previously reported connected, but this audit did not modify or query production database data. Notification delivery currently consists of persisted in-app records only; email/browser delivery is not configured or claimed. Historical analytics use persisted data and omit missing buckets; they do not fabricate samples. No universal IAQ score is produced.
