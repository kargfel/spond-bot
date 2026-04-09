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
    # Internal API key for direct backend access (never sent to the browser)
    api_key: str

    # The public domain the app is served on — used for secure cookie settings.
    # e.g. "spond.felixkarg.de"
    site_domain: str = "localhost"

    # Scheduler
    discovery_interval_minutes: int = 60
    executioner_interval_seconds: int = 60

    # Timezone (used by APScheduler)
    tz: str = "Europe/Berlin"

    # Admin dashboard account — seeded once on startup if no admin exists
    admin_username: str = "admin"
    admin_password: str = "changeme"


settings = Settings()
