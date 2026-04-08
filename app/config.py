"""
Central configuration — all env vars are defined here via pydantic-settings.
Import `settings` from this module rather than calling os.getenv() directly.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str

    # Security
    fernet_key: str
    api_key: str

    # Scheduler
    discovery_interval_minutes: int = 60
    executioner_interval_seconds: int = 60

    # Timezone (used by APScheduler)
    tz: str = "Europe/Berlin"


settings = Settings()
