"""
Application Settings - Pydantic v2 Settings Management
All config comes from environment variables / .env file
"""

from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Unified Storage Monitoring"
    app_version: str = "3.0.0"
    debug: bool = False
    log_dir: str = "/app/logs"

    # API
    api_v1_prefix: str = "/api/v1"
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Security / JWT  (auth not enforced yet — populated but unused until Phase 3)
    secret_key: str = Field(default="CHANGE_ME_BEFORE_PRODUCTION_USE", alias="SECRET_KEY")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours

    # SQL Server
    sql_server: str = Field(default="usidcvsql0252.ctl.intranet", alias="SQL_SERVER")
    sql_database: str = Field(default="StorMart", alias="SQL_DATABASE")
    db_schema: str = Field(default="USM", alias="DB_SCHEMA")
    sql_driver: str = Field(default="ODBC Driver 17 for SQL Server", alias="SQL_DRIVER")

    # KeePass
    keepass_url: str = Field(
        default="http://usodclpsandadm1.corp.intranet:2000/keepass",
        alias="KEEPASS_URL",
    )
    sql_cred_key: str = Field(default="SQLServerDB", alias="SQL_CRED_KEY")
    pure_cred_key: str = Field(default="PureStorage", alias="PURE_CRED_KEY")

    # Notifications
    teams_webhook_url: str = Field(default="", alias="TEAMS_WEBHOOK_URL")
    # Comma-separated severities that trigger a Teams alert (critical,warning,info)
    teams_severities: str = Field(default="critical,warning", alias="TEAMS_SEVERITIES")
    snow_enabled: bool = Field(default=False, alias="SNOW_ENABLED")

    # Capacity alerting — per-array threshold crossings + fleet projected-full
    capacity_alerts_enabled: bool = Field(default=True, alias="CAPACITY_ALERTS_ENABLED")
    # Comma-separated utilization % thresholds that trigger a per-array Teams alert
    capacity_alert_thresholds: str = Field(default="80,90,95", alias="CAPACITY_ALERT_THRESHOLDS")
    # Minimum days between re-sending the same threshold/projection alert
    capacity_alert_resend_days: int = Field(default=7, alias="CAPACITY_ALERT_RESEND_DAYS")
    # Alert when the fleet is projected to fill within this many days
    capacity_projected_full_days: int = Field(default=30, alias="CAPACITY_PROJECTED_FULL_DAYS")
    # Trailing window (days of daily_stats) used to compute the growth projection
    capacity_alert_trend_days: int = Field(default=90, alias="CAPACITY_ALERT_TREND_DAYS")
    # How often the scheduler runs the capacity alert check (hours)
    capacity_alert_check_interval_hours: int = Field(default=12, alias="CAPACITY_ALERT_CHECK_INTERVAL_HOURS")



    # Collector intervals (seconds)
    metrics_interval: int = Field(default=300, alias="METRICS_INTERVAL")
    volumes_interval: int = Field(default=1800, alias="VOLUMES_INTERVAL")
    alerts_interval: int = Field(default=300, alias="ALERTS_INTERVAL")

    # Alert lifecycle
    alert_resolve_days: int = Field(default=7, alias="ALERT_RESOLVE_DAYS")
    alert_purge_days: int = Field(default=30, alias="ALERT_PURGE_DAYS")

    # Metrics history retention (days). Extended to 365 to support YTD
    # capacity-growth analytics. Set lower to reclaim space.
    history_retention_days: int = Field(default=365, alias="HISTORY_RETENTION_DAYS")


    # Chat / Ollama (local LLM)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:3b", alias="OLLAMA_MODEL")
    chat_enabled: bool = Field(default=True, alias="CHAT_ENABLED")
    chat_query_timeout: int = Field(default=10, alias="CHAT_QUERY_TIMEOUT")

    # Arrays config file
    arrays_config_file: str = Field(default="/app/config/arrays.txt", alias="ARRAYS_CONFIG_FILE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
