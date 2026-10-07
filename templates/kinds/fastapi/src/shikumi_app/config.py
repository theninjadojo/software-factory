"""Typed configuration, read from the environment (and a local .env file). A bad value stops start-up."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/shikumi_app"
    # Origins the browser and app front ends call from, as a JSON list: ["https://app.example.com"]
    cors_origins: list[str] = []
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Seconds the worker waits before looking for jobs again when the queue is empty.
    worker_poll_seconds: float = 1.0

    @field_validator("database_url")
    @classmethod
    def use_psycopg(cls, url: str) -> str:
        """Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
