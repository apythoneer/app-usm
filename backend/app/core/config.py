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
    app_version: str = "2.0.0"
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
    snow_enabled: bool = Field(default=False, alias="SNOW_ENABLED")

    # Collector intervals (seconds)
    metrics_interval: int = Field(default=60, alias="METRICS_INTERVAL")
    volumes_interval: int = Field(default=900, alias="VOLUMES_INTERVAL")
    alerts_interval: int = Field(default=300, alias="ALERTS_INTERVAL")

    # Arrays config file
    arrays_config_file: str = Field(default="/app/config/arrays.txt", alias="ARRAYS_CONFIG_FILE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
