import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, get_db
from app.config import Settings
from app.models import Device, Reading


class FakeSession:
    def __init__(self, fail=False):
        self.fail = fail

    def execute(self, statement):
        if self.fail:
            raise RuntimeError("private database connection detail")

    def query(self, model):
        if self.fail:
            raise RuntimeError("private database connection detail")


@pytest.fixture

def client():
    app = main.app
    app.dependency_overrides[get_db] = lambda: iter([FakeSession()])
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_liveness_does_not_depend_on_database(client):
    main.app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(AssertionError("DB should not be requested"))
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "unchecked"}


def test_readiness_reports_database_failure_without_details(client):
    main.app.dependency_overrides[get_db] = lambda: iter([FakeSession(fail=True)])
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "database_unavailable", "message": "Database is unavailable"}
    assert "private" not in response.text


def test_latest_returns_persisted_reading_and_marks_old_backfill_stale(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'latest.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    stale_timestamp = datetime.now(timezone.utc) - timedelta(minutes=20)
    with factory() as session:
        session.add(Reading(
            device_id="sensor-1", provider_entry_id=9, provider_entry_key="sensor-1:9",
            timestamp=stale_timestamp, co2=720, source="thingspeak", source_metadata={},
            ingestion_mode="historical_backfill",
        ))
        session.commit()

    app = main.create_app(app_settings=Settings(_env_file=None, ingestion_enabled=False), session_factory=factory)
    def override_db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/readings/latest")
    app.dependency_overrides.clear()
    engine.dispose()

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "airshield_database" and body["stale"] is True
    assert body["readings"][0]["timestamp"].startswith(stale_timestamp.isoformat()[:19])
    assert body["readings"][0]["ingestion_mode"] == "historical_backfill"


def test_latest_empty_persisted_store_is_stale_without_calling_provider(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'empty-latest.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = main.create_app(app_settings=Settings(_env_file=None, ingestion_enabled=False), session_factory=factory)
    def override_db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/readings/latest")
    app.dependency_overrides.clear()
    engine.dispose()
    assert response.status_code == 200
    assert response.json()["readings"] == [] and response.json()["stale"] is True


def test_latest_database_failure_is_safe(client):
    main.app.dependency_overrides[get_db] = lambda: iter([FakeSession(fail=True)])
    response = client.get("/api/v1/readings/latest")
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "database_unavailable", "message": "Persisted readings are unavailable"}
    assert "private" not in response.text


def test_cors_uses_configured_origins(client):
    response = client.options("/api/v1/health/live", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_settings_accept_comma_separated_cors_and_empty_optional_credentials():
    settings = Settings(_env_file=None, cors_origins="https://one.example, https://two.example", thingspeak_read_api_key="", alerts_admin_token="")
    assert settings.allowed_origins == ["https://one.example", "https://two.example"]
    assert settings.thingspeak_read_api_key is None
    assert settings.alerts_admin_token is None


def test_thingspeak_credentials_have_no_source_code_defaults():
    settings = Settings(_env_file=None)
    assert settings.thingspeak_read_api_key is None
    assert settings.thingspeak_write_api_key is None


def test_httpx_info_logging_is_suppressed_to_protect_query_credentials():
    import logging
    import app.main  # noqa: F401

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_database_models_define_expected_indexes_and_deduplication():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    reading_indexes = {index["name"] for index in inspect(engine).get_indexes("readings")}
    assert {"ix_readings_device_id", "ix_readings_timestamp"} <= reading_indexes
    with Session(engine) as session:
        session.add(Device(id="d1", name="Sensor"))
        session.add_all([
            Reading(device_id="d1", provider_entry_id=7, timestamp=datetime(2026, 10, 28, 10, tzinfo=timezone.utc), source="thingspeak", source_metadata={}),
            Reading(device_id="d1", provider_entry_id=7, timestamp=datetime(2026, 10, 28, 10, 0, 1, tzinfo=timezone.utc), source="thingspeak", source_metadata={}),
        ])
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()
