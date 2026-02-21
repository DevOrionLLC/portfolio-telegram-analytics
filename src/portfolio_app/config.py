from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TELEGRAM_BOT_TOKEN: str

    BASE_DIR: str = "/opt/portfolio-telegram-analytics"
    VAR_DIR: str = "/opt/portfolio-telegram-analytics/var"
    UPLOAD_DIR: str = "/opt/portfolio-telegram-analytics/var/uploads"
    REPORT_DIR: str = "/opt/portfolio-telegram-analytics/var/reports"
    CACHE_DIR: str = "/opt/portfolio-telegram-analytics/var/cache"

    DATABASE_URL: str = "sqlite:////opt/portfolio-telegram-analytics/var/app.sqlite3"

    RISK_FREE_RATE_ANNUAL: float = 0.02
    BENCHMARK_SP500: str = "SPY"
    BENCHMARK_R2000: str = "IWM"
    HISTORY_MONTHS: int = 3
    MAX_UPLOAD_MB: int = 10

    # Optional OpenClaw webhooks
    OPENCLAW_HOOKS_URL: str | None = None
    OPENCLAW_HOOKS_TOKEN: str | None = None
    OPENCLAW_HOOKS_AGENTID: str = "hooks"
    OPENCLAW_HOOKS_DELIVER: bool = False
    OPENCLAW_HOOKS_CHANNEL: str = "telegram"
    OPENCLAW_HOOKS_TO: str | None = None


settings = Settings()