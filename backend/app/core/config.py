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

    # Process role. The scheduler + collectors currently run INSIDE the API
    # process, which forces a single uvicorn worker (multiple workers would each
    # run the scheduler -> duplicate collection). This flag lets the same image
    # run in two roles:
    #   RUN_SCHEDULER=true  (default) — hosts the scheduler/collectors. Run ONE.
    #   RUN_SCHEDULER=false           — API only; safe to run with N uvicorn workers.
    # Default true so existing single-container deployments are unchanged. The
    # split into an api-only + collector container is opt-in via compose.
    run_scheduler: bool = Field(default=True, alias="RUN_SCHEDULER")

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
    # Only Teams-notify alerts opened within this window. Prevents a notification
    # storm when a backlog of long-open alerts is first ingested (e.g. the Pure
    # fetch fix that surfaced dozens of alerts opened years ago) — they are still
    # recorded and shown, just not re-paged. Genuinely new alerts appear within a
    # poll cycle of opening, well inside this window.
    alert_notify_max_age_hours: int = Field(default=24, alias="ALERT_NOTIFY_MAX_AGE_HOURS")
    # HPE alerts live in the WSAPI event log (there is no /alerts resource); we pull
    # this many days of the log and keep category==ALERT entries. The log only
    # retains ~2 weeks, so a value past that just returns everything available.
    hpe_alert_lookback_days: int = Field(default=30, alias="HPE_ALERT_LOOKBACK_DAYS")
    # Hitachi VSP SIM alerts accumulate (up to 10240) and never drop off, so we keep
    # only alerts within this recent window as "active"; older ones age out (and the
    # absence-based resolver clears them). Same idea as the HPE event log.
    hitachi_alert_lookback_days: int = Field(default=30, alias="HITACHI_ALERT_LOOKBACK_DAYS")

    # Metrics history retention (days). Extended to 365 to support YTD
    # capacity-growth analytics. Set lower to reclaim space.
    # Applies to metrics_history: ~90 rows per collection, so 365d is cheap.
    history_retention_days: int = Field(default=365, alias="HISTORY_RETENTION_DAYS")

    # volumes_history retention (days) — deliberately SEPARATE from, and much
    # shorter than, history_retention_days.
    #
    # volumes_history is ~44k rows per collection (one per volume) against
    # metrics_history's ~90 (one per array) — roughly 500x the write volume, or
    # ~1.9M rows/day at current fleet size. Reusing the 365d metrics window would
    # mean ~700M rows / ~90GB. It is kept per-collection rather than daily because
    # the intra-day resolution feeds volume anomaly detection, so the cost has to
    # be paid here, in the retention window, rather than by discarding resolution.
    #
    # Sizing at current fleet size (~44k volumes, 48 collections/day), measured
    # from the live table (5,465 MB / 42.26M rows => ~0.13 KB/row):
    #     30d  ~=  57M rows  /  ~7 GB
    #     90d  ~= 171M rows  / ~22 GB
    #    180d  ~= 342M rows  / ~44 GB   <-- current setting
    #    365d  ~= 700M rows  / ~90 GB
    #
    # 180d is a deliberate choice: it buys two full quarters of intra-day baseline
    # for anomaly detection. It is not free — budget ~44GB and expect the nightly
    # cleanup to delete ~1.9M rows/day once the window fills (from ~2027-01-11;
    # the data only starts at 2026-06-19). Watch index maintenance on this table.
    volume_history_retention_days: int = Field(
        default=180, alias="VOLUME_HISTORY_RETENTION_DAYS"
    )


    # Chat / Ollama (local LLM — CPU, in-container)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:3b", alias="OLLAMA_MODEL")
    chat_enabled: bool = Field(default=True, alias="CHAT_ENABLED")
    chat_query_timeout: int = Field(default=10, alias="CHAT_QUERY_TIMEOUT")
    # Max concurrent chat requests. The backend is a single uvicorn worker that
    # also hosts the scheduler; unbounded chat (each holding a worker thread up to
    # 180s waiting on the LLM) can starve the API and flip the container
    # unhealthy — which is exactly what happened. Excess requests get a fast 503.
    chat_max_concurrent: int = Field(default=2, alias="CHAT_MAX_CONCURRENT")

    # ── Second chat backend: DGX Spark (benchmark / POC) ─────────────────────
    # Used to compare the CPU-hosted qwen2.5:3b against dedicated GPU hardware
    # running qwen3:30b-a3b, to evidence a hardware business case.
    #
    # NOTE ON DATA EGRESS: unlike the local backend, this one sends question text
    # AND SQL result rows (array names, volume names, capacities) to an external
    # host over the public internet. docker-compose still describes chat as
    # "local LLM — no data leaves"; that stops being true for any request routed
    # here. Left empty by default so this is opt-in, never accidental.
    chat_dgx_base_url: str = Field(default="", alias="CHAT_DGX_BASE_URL")
    chat_dgx_model: str = Field(default="qwen3:30b-a3b", alias="CHAT_DGX_MODEL")

    # Cloudflare Access service token for the DGX endpoint. It sits behind
    # Zero Trust, which 302s unauthenticated calls to an IdP login page — so
    # without these every request silently becomes an HTML redirect, not JSON.
    # The Access app also needs a policy with action "Service Auth"; a token
    # alone is not sufficient.
    cf_access_client_id: str = Field(default="", alias="CF_ACCESS_CLIENT_ID")
    cf_access_client_secret: str = Field(default="", alias="CF_ACCESS_CLIENT_SECRET")

    # Partial-collection data-loss guard.
    #
    # Delete-then-insert collectors (hitachi/hpe/dell/oracle) DELETE an array's
    # cached rows then INSERT what was just collected. A collection that returns a
    # PARTIAL set — e.g. a Hitachi LDEV query that times out mid-pagination and
    # yields 50 of 3,000 volumes — is non-empty, so the existing "is it empty?"
    # guard passes and the array's real inventory is replaced with the fragment.
    #
    # This refuses the destructive replace when the incoming row count is below
    # this fraction of what is already stored for the array (which is far more
    # likely a failed/partial collect than a real >50% shrink). Set to 0 to
    # disable. A genuine large shrink (decommission) just needs one retry or a
    # manual clear; silent data loss does not get a second chance.
    collect_shrink_min_ratio: float = Field(default=0.5, alias="COLLECT_SHRINK_MIN_RATIO")

    # Arrays config file
    arrays_config_file: str = Field(default="/app/config/arrays.txt", alias="ARRAYS_CONFIG_FILE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
