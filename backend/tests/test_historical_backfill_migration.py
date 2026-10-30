from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_backfill_migration_preserves_existing_readings_and_defaults_them_live(tmp_path, monkeypatch):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    from app.config import get_settings

    get_settings.cache_clear()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "0002_ingestion_alerts")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO devices (id, name) VALUES ('sensor-1', 'Sensor')"))
            connection.execute(text(
                "INSERT INTO readings (provider_entry_id, provider_entry_key, device_id, timestamp, co2, source, source_metadata) "
                "VALUES (41, 'sensor-1:41', 'sensor-1', '2026-10-01 08:35:02', 510, 'thingspeak', '{}')"
            ))

        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT provider_entry_id, provider_entry_key, timestamp, co2, ingestion_mode "
                "FROM readings"
            )).one()
        assert row.provider_entry_id == 41
        assert row.provider_entry_key == "sensor-1:41"
        assert "2026-10-01 08:35:02" in row.timestamp
        assert row.co2 == 510
        assert row.ingestion_mode == "live"

        # Existing environmental alerts keep their environmental default and
        # nullable health fields permit a state alert without fabricated links.
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO alert_rules (id, parameter, operator, threshold, unit, severity, enabled, persistence_seconds, message, recommended_action) "
                "VALUES ('rule-1', 'co2', 'gt', 1000, 'ppm', 'warning', 1, 0, 'High CO2', 'Ventilate')"
            ))
            connection.execute(text(
                "INSERT INTO alerts (device_id, rule_id, reading_id, status, parameter, observed_value, threshold, unit, severity, message, recommended_action, created_at, condition_started_at) "
                "VALUES ('sensor-1', 'rule-1', (SELECT id FROM readings LIMIT 1), 'active', 'co2', 1200, 1000, 'ppm', 'warning', 'High CO2', 'Ventilate', '2026-10-01 08:35:02', '2026-10-01 08:35:02')"
            ))
            connection.execute(text(
                "INSERT INTO alerts (device_id, rule_id, reading_id, category, health_state, status, parameter, unit, severity, message, recommended_action, created_at, condition_started_at) "
                "VALUES ('sensor-1', NULL, NULL, 'device_health', 'stale', 'active', 'device_health', 'state', 'warning', 'Device stale', 'Check device', '2026-10-01 08:36:02', '2026-10-01 08:35:02')"
            ))
        with engine.connect() as connection:
            alerts = connection.execute(text("SELECT category, health_state, rule_id, reading_id, observed_value, threshold FROM alerts ORDER BY id")).all()
        assert alerts[0] == ("environmental", None, "rule-1", 1, 1200.0, 1000.0)
        assert alerts[1] == ("device_health", "stale", None, None, None, None)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO alerts (device_id, rule_id, reading_id, category, health_state, status, parameter, unit, severity, message, recommended_action, created_at, condition_started_at) "
                    "VALUES ('sensor-1', NULL, NULL, 'device_health', 'stale', 'active', 'device_health', 'state', 'warning', 'Duplicate', 'Check device', '2026-10-01 08:37:02', '2026-10-01 08:35:02')"
                ))

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO readings (device_id, timestamp, source, source_metadata, ingestion_mode) "
                    "VALUES ('sensor-1', '2026-10-01 08:36:02', 'thingspeak', '{}', 'invalid')"
                ))
    finally:
        engine.dispose()
        get_settings.cache_clear()
