"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings with sensible defaults for local development."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./certificates.db"
    certificates_dir: Path = Path("./certificates")
    max_recipients: int = 1000
    log_level: str = "INFO"

    # When True, BackgroundTasks are skipped and processing is left to the caller.
    # Tests set this so they can drive processing synchronously.
    sync_processing: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
