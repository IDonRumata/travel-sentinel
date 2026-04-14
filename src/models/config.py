"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All settings come from .env - zero hardcoded secrets."""

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "travel_sentinel"
    postgres_user: str = "sentinel"
    postgres_password: str  # no default - must be set

    # External APIs
    brave_search_api_key: str | None = None  # Optional: if None, uses local KNOWN_VISA_FREE_BY only
    aviasales_token: str  # no default - must be set

    # Telegram
    telegram_bot_token: str  # no default - must be set
    telegram_chat_id: str  # no default - must be set

    # App tuning
    log_level: str = "INFO"
    scrape_interval_minutes: int = 60
    price_drop_threshold_percent: int = 10
    max_price_per_person_usd: int = 400
    adults: int = 2
    passport_type: str = "BY"  # only Belarusian passports

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
