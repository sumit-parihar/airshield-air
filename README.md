# AirShield

AirShield pairs the FastAPI backend with a React and TypeScript monitoring interface. The browser talks only to the AirShield API; ThingSpeak credentials and ingestion remain server-side. Live values, alerts, device status, rules, notifications, and historical statistics are shown only when returned by the backend.

Use the Vite application and FastAPI commands below for the supported product. The root-level `airshield.py` and legacy static files are an earlier presentation prototype containing synthetic examples; they are not connected to AirShield data and must not be used for monitoring or a live-data demonstration.

## Run locally on Windows PowerShell

Use two terminals from the repository root.

Install Node.js 20.19 or newer for Vite 7, and Python 3.11 or newer for the backend.

### Backend terminal

```powershell
cd .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `backend\.env` with your PostgreSQL `DATABASE_URL`, ThingSpeak server-side credentials, and device metadata. Keep it private and out of source control. Set `INGESTION_ENABLED=true` to poll the configured channel. Configure `THINGSPEAK_VOC_UNIT` only after confirming sensor semantics. Then migrate and launch:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

API docs are at `http://localhost:8000/docs`. The backend CORS default permits `http://localhost:5173`.

### Frontend terminal

```powershell
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://localhost:5173`). `VITE_API_BASE_URL` defaults to `http://localhost:8000/api/v1`; `VITE_POLL_INTERVAL_MS` defaults to 30000 and is clamped to at least 15000 ms. These frontend settings contain no credentials. If `ALERTS_ADMIN_TOKEN` is configured on the backend, enter it into the in-app Admin access field for the current browser session. It is held in memory and cleared on refresh.

## API-backed sections

- **Overview and Live monitoring** use `/readings/latest`, `/alerts/active`, `/devices`, `/alert-rules`, and `/analytics/trends`. The latest endpoint reads the newest persisted database sample; ThingSpeak is polled only by the backend ingestion loop.
- **Historical analytics** uses `/analytics/summary` and `/analytics/trends`. It reports persisted aggregates and conservative observed exceedance intervals for enabled rules. Gaps and unobserved time before/after samples are omitted.
- **Alert center** uses active and paginated `/alerts` queries with backend acknowledge and resolve operations.
- **Devices** uses `/devices` and server-computed freshness status.
- **Settings** uses `/alert-rules` for create, update, enable, and disable and `/notifications/in-app` for recorded notification status. Browser and email delivery are marked unavailable unless backend providers are implemented.

No universal IAQ score is calculated. Threshold rules are project operational settings and require review against sensor behavior and suitable guidance. The dashboard does not certify that air is safe.

### Review-ready operational humidity rules

Two initial humidity rules are stored in the PostgreSQL database: `>60 %RH` (warning) and `<30 %RH` (informational), each with 600 seconds persistence and recovery hysteresis at `55 %RH` / `35 %RH`. The rationale is EPA home/building guidance to keep indoor relative humidity between 30% and 50% where practical and below 60%; these are project alert boundaries for moisture management, not an indoor-air compliance standard or a health determination. Recommendations cite the [EPA indoor air quality guide](https://www.epa.gov/indoor-air-quality-iaq/care-your-air-guide-indoor-air-quality). Review the rules against the actual humidity sensor calibration and deployment context. VOC remains unconfigured because its sensor units are unverified.

## Frontend checks

```powershell
npm run typecheck
npm test
npm run build
```

## Database and verification notes

The backend README documents migrations, tests, and environment configuration. PostgreSQL and real hardware should be verified in the deployment environment; SQLite test results do not establish PostgreSQL compatibility or live ThingSpeak behavior.

For the end-to-end setup, troubleshooting, backup, and tomorrow's hardware verification procedure, see [RUNBOOK.md](RUNBOOK.md). The ESP32 folder contains a hardware-neutral publishing scaffold; it is not a tested firmware build and deliberately has no assumed sensor models or pin assignments.
