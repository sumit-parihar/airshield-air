from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://airshield:change-me@localhost:5432/airshield"
    cors_origins: str = "http://localhost:5173"
    thingspeak_channel_id: int = Field(default=3509505, gt=0)
    thingspeak_read_api_key: str | None = None
    thingspeak_write_api_key: str | None = None
    thingspeak_voc_unit: str | None = Field(default=None, max_length=32)
    thingspeak_timeout_seconds: float = Field(default=8, gt=0, le=60)
    thingspeak_batch_size: int = Field(default=100, ge=1, le=8000)
    thingspeak_poll_interval_seconds: int = Field(default=30, ge=15, le=3600)
    thingspeak_device_id: str | None = Field(default=None, max_length=128)
    thingspeak_device_name: str = Field(default="AirShield ThingSpeak device", min_length=1, max_length=200)
    thingspeak_device_location: str | None = Field(default=None, max_length=200)
    ingestion_enabled: bool = True
    reading_stale_after_seconds: int = Field(default=900, gt=0)
    alerts_admin_token: SecretStr | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("thingspeak_read_api_key", "thingspeak_write_api_key", "thingspeak_voc_unit", "thingspeak_device_id", "thingspeak_device_location", "alerts_admin_token", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
