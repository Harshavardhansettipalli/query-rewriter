"""Application settings.

All configuration comes from environment variables or the `.env` file in the
project root. Nothing secret is ever hard-coded, and the OpenAI API key is only
read on the server side - it is never sent to the browser.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = (PROJECT_ROOT / "data" / "app.db").as_posix()

# Placeholder values people often forget to replace in .env
_PLACEHOLDER_KEYS = {"", "sk-your-key-here", "your-api-key", "changeme"}


class Settings(BaseSettings):
    # The .env path is absolute so the app works no matter which folder you start it from.
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Search Query Rewriter Agent"
    environment: str = "development"
    log_level: str = "INFO"

    # --- OpenAI -----------------------------------------------------------
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 20.0
    openai_max_retries: int = 2
    # Leave empty to not send a temperature (some newer models only accept the default).
    openai_temperature: float | None = 0.1

    # --- Storage ----------------------------------------------------------
    database_url: str = f"sqlite:///{DEFAULT_SQLITE_PATH}"

    # --- Pipeline rules (the "Python" half of the hybrid design) -----------
    min_query_length: int = 2
    max_query_length: int = 300
    max_rewrite_words: int = 25
    duplicate_window_seconds: int = 300
    recent_activity_limit: int = 20
    context_activity_limit: int = 5

    # --- Web --------------------------------------------------------------
    rate_limit_per_minute: int = 30
    cors_origins: str = ""  # comma-separated; empty = same-origin only

    @field_validator("openai_temperature", mode="before")
    @classmethod
    def _empty_to_none(cls, value):
        # A line like OPENAI_TEMPERATURE= would otherwise crash float parsing at startup.
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _fix_postgres_scheme(cls, value):
        # Some hosting tools hand out "postgres://", which SQLAlchemy 2 rejects.
        if isinstance(value, str) and value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://"):]
        return value

    @property
    def llm_enabled(self) -> bool:
        return self.openai_api_key.strip() not in _PLACEHOLDER_KEYS

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
