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
    site_domain: str = "localhost"

    # Scheduler
    discovery_interval_minutes: int = 60
    executioner_interval_seconds: int = 60
    rsvp_lead_time_ms: int = 0

    # Database connection pool. Answers for one opening are written at the same moment, so the pool
    # must hold enough warm connections for all members at once (see database.warm_pool).
    db_pool_size: int = 30
    db_max_overflow: int = 20

    # Timezone (used by APScheduler)
    tz: str = "Europe/Berlin"

    # Web Push (optional). Generate with: python scripts/generate_vapid_key.py
    # Without a key the notification option is hidden everywhere.
    vapid_private_key: str = ""
    # Contact for push services (mailto: or https: URL). Defaults to https://<SITE_DOMAIN>.
    vapid_subject: str = ""

    # Comma-separated IPs/CIDRs of the reverse proxy whose X-Forwarded-For is believed.
    # Read by uvicorn through the Dockerfile (unset = only 127.0.0.1); declared here so the app
    # can warn when it is unset or "*".
    trusted_proxies: str = ""

    # Audit trail: who did what, when, from where. Entries older than the retention
    # (in days) are deleted nightly; IP addresses are personal data, so keep it short.
    audit_enabled: bool = True
    audit_retention_days: int = 90

    # Admin dashboard account — seeded once on startup if no admin exists
    admin_username: str = "admin"
    admin_password: str = "changeme"


settings = Settings()
