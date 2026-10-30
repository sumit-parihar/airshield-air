import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api import router
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.ingestion import ThingSpeakIngestionService
from app.models import Reading
from app.schemas import HealthResponse, LatestReadingsResponse
from app.thingspeak import ThingSpeakClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()


def create_app(
    app_settings=settings,
    session_factory: sessionmaker[Session] = SessionLocal,
    client_factory=ThingSpeakClient,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = None
        stop_event = None
        if app_settings.ingestion_enabled and app_settings.app_env.lower() != "test":
            stop_event = asyncio.Event()
            service = ThingSpeakIngestionService(app_settings, session_factory, client_factory(
                app_settings.thingspeak_channel_id,
                app_settings.thingspeak_read_api_key,
                app_settings.thingspeak_timeout_seconds,
                app_settings.thingspeak_voc_unit,
            ))
            task = asyncio.create_task(service.run(stop_event), name="airshield-thingspeak-ingestion")
        try:
            yield
        finally:
            if task is not None and stop_event is not None:
                stop_event.set()
                try:
                    await asyncio.wait_for(task, timeout=app_settings.thingspeak_timeout_seconds + 7)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    application = FastAPI(
        title="AirShield API",
        version="0.2.0",
        description="AirShield monitoring API. Administrative routes require the configured bearer token in production; deploy behind TLS and additional access controls.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(_, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "validation_error", "message": "Request validation failed", "errors": jsonable_encoder(exc.errors())}},
        )

    @application.get("/api/v1/health/live", response_model=HealthResponse)
    def liveness() -> HealthResponse:
        return HealthResponse(status="ok", database="unchecked")

    @application.get("/api/v1/health/ready", response_model=HealthResponse)
    def readiness(db: Session = Depends(get_db)) -> HealthResponse:
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:
            logging.getLogger(__name__).warning("Database readiness check failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail={"code": "database_unavailable", "message": "Database is unavailable"}) from exc
        return HealthResponse(status="ok", database="ready")

    @application.get("/api/v1/readings/latest", response_model=LatestReadingsResponse)
    def latest_readings(db: Session = Depends(get_db)) -> LatestReadingsResponse:
        """Return the newest persisted sample; ThingSpeak is polled only by ingestion."""
        try:
            readings = (
                db.query(Reading)
                .order_by(Reading.timestamp.desc(), Reading.id.desc())
                .limit(1)
                .all()
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("Latest persisted reading query failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail={"code": "database_unavailable", "message": "Persisted readings are unavailable"},
            ) from exc
        now = datetime.now(timezone.utc)
        timestamp = readings[0].timestamp if readings else None
        if timestamp is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        stale = timestamp is None or (now - timestamp).total_seconds() > app_settings.reading_stale_after_seconds
        return LatestReadingsResponse(readings=readings, source="airshield_database", fetched_at=now, stale=stale)

    application.include_router(router)
    return application


app = create_app()
