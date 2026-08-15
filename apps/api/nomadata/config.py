"""Typed application settings.

Single source of truth for configuration. Secrets live here (loaded from the
environment) and are injected into connectors/providers at the composition
root — they never reach the agent or the LLM.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nomadata import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOMADATA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Application metadata database (NomaData's own storage).
    database_url: str = "postgresql://nomadata:nomadata@localhost:5432/nomadata"

    # Cube semantic/query layer.
    cube_url: str = "http://localhost:4000"

    # AI provider (interface only in M0 — not called yet).
    ai_provider: str = "openai_compatible"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"

    # Primary data source (M1 — one source configured via env; multiple in M5).
    # ds_kind: "mysql" | "sqlserver" | "" (disabled).
    ds_kind: str = ""
    ds_name: str = "primary"
    ds_host: str = "localhost"
    ds_port: int = 3306
    ds_database: str = ""
    ds_user: str = ""
    ds_password: str = ""

    @property
    def data_source_configured(self) -> bool:
        return bool(self.ds_kind and self.ds_database)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Accept a comma-separated string from the environment.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def version(self) -> str:
        return __version__


@lru_cache
def get_settings() -> Settings:
    return Settings()
