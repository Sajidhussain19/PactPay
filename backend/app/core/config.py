"""Application settings loaded from environment variables / .env.

All secrets live in the environment, never in code. Import `get_settings()`
instead of instantiating `Settings` directly so tests can override it.
"""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PAYPAL_BASE_URLS: dict[str, str] = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "PactPay"
    app_env: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Database (SQLite to start, Postgres in docker-compose / Render)
    database_url: str = "sqlite+aiosqlite:///./pactpay.db"

    # PayPal
    paypal_mode: Literal["sandbox", "live"] = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: SecretStr = SecretStr("")
    paypal_webhook_id: str = ""
    paypal_currency: str = "USD"
    platform_fee_percent: Decimal = Decimal("5.0")

    # AI
    anthropic_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-sonnet-5-5"
    judge_confidence_threshold: float = 0.8

    @property
    def paypal_base_url(self) -> str:
        return PAYPAL_BASE_URLS[self.paypal_mode]


@lru_cache
def get_settings() -> Settings:
    return Settings()
