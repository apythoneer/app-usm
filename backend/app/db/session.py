"""
Database session management — pyodbc connection pool for SQL Server.
FastAPI uses run_in_threadpool to keep async handlers non-blocking.

Uses pyodbc's built-in connection pooling for efficient connection reuse.
"""

import logging
import threading
import pyodbc
from contextlib import contextmanager
from typing import Generator, Optional

from app.core.config import get_settings
from app.services.keepass import get_sql_credentials

logger = logging.getLogger("usm.db")
settings = get_settings()
SCHEMA = settings.db_schema

# Enable pyodbc's built-in connection pooling
pyodbc.pooling = True

# Cache the connection string (thread-safe, credentials rarely change)
_conn_str_cache: Optional[str] = None
_conn_str_lock = threading.Lock()


def _build_conn_str(username: str, password: str) -> str:
    preferred = [settings.sql_driver, "ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
    available = pyodbc.drivers()
    driver = next((d for d in preferred if d in available), preferred[-1])
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={settings.sql_server};"
        f"DATABASE={settings.sql_database};"
        f"UID={username};PWD={password};"
        "TrustServerCertificate=yes;Connection Timeout=60;"
    )


def _get_conn_str() -> str:
    """Get cached connection string, building it once from KeePass."""
    global _conn_str_cache
    if _conn_str_cache:
        return _conn_str_cache
    with _conn_str_lock:
        if not _conn_str_cache:
            creds = get_sql_credentials()
            _conn_str_cache = _build_conn_str(creds["username"], creds["password"])
    return _conn_str_cache


def invalidate_conn_str_cache():
    """Call after credential refresh to rebuild connection string."""
    global _conn_str_cache
    with _conn_str_lock:
        _conn_str_cache = None


def get_connection() -> pyodbc.Connection:
    """Get a pooled pyodbc connection (credentials cached from KeePass)."""
    conn_str = _get_conn_str()
    conn = pyodbc.connect(conn_str, autocommit=False)
    conn.timeout = 60  # increased from 30 for heavy queries
    return conn


@contextmanager
def get_db_cursor() -> Generator:
    """
    Context manager yielding a cursor with auto commit/rollback.
    Uses connection pooling for efficient reuse.
    Use inside sync functions called via run_in_threadpool.
    """
    conn: Optional[pyodbc.Connection] = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass  # connection may already be dead
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass  # pooling handles cleanup


def row_to_dict(cursor: pyodbc.Cursor, row) -> Optional[dict]:
    if row is None:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def rows_to_dicts(cursor: pyodbc.Cursor, rows) -> list[dict]:
    if not rows:
        return []
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


def test_connection() -> bool:
    """Health check — returns True if DB is reachable."""
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Schema initialization — idempotent (IF NOT EXISTS on every object)
# Safe to call on every startup; creates tables only if they don't exist.
# ---------------------------------------------------------------------------

def init_database() -> None:
    """
    Create the USM schema and all tables if they don't already exist.
    Called once at application startup via the FastAPI lifespan handler.
    Mirrors the exact schema from v1 collectors/common/db.py.
    """
    logger.info(f"Initializing database schema [{SCHEMA}]...")

    with get_db_cursor() as cursor:

        # Schema
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = '{SCHEMA}')
                EXEC('CREATE SCHEMA [{SCHEMA}]')
        """)

        # ------------------------------------------------------------------
        # messages — alert history + notification tracking
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='messages' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.messages (
                id                INT IDENTITY(1,1) PRIMARY KEY,
                array_name        NVARCHAR(255) NOT NULL,
                vendor            NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                message_id        INT           NOT NULL,
                event             NVARCHAR(500),
                severity          NVARCHAR(50),
                component_type    NVARCHAR(100),
                component_name    NVARCHAR(255),
                opened            NVARCHAR(50),
                closed            NVARCHAR(50),
                expected          NVARCHAR(500),
                actual            NVARCHAR(500),
                collected_at      NVARCHAR(50),
                alerted           NVARCHAR(50),
                teams_notified    DATETIME2,
                snow_ticket       NVARCHAR(100),
                suppressed        BIT DEFAULT 0,
                resolved          BIT DEFAULT 0,
                CONSTRAINT UK_messages UNIQUE (array_name, message_id)
            )
        """)

        # ------------------------------------------------------------------
        # metrics_current — latest snapshot per array (upserted every poll)
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='metrics_current' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.metrics_current (
                id                  INT IDENTITY(1,1) PRIMARY KEY,
                array_name          NVARCHAR(255) NOT NULL UNIQUE,
                vendor              NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                purity_version      NVARCHAR(50),
                read_latency_us     INT,
                write_latency_us    INT,
                read_iops           INT,
                write_iops          INT,
                read_bandwidth      BIGINT,
                write_bandwidth     BIGINT,
                capacity_total      BIGINT,
                capacity_used       BIGINT,
                capacity_used_pct   FLOAT,
                data_reduction      FLOAT,
                total_reduction     FLOAT,
                shared_space        BIGINT,
                snapshot_space      BIGINT,
                volume_space        BIGINT,
                array_status        NVARCHAR(50),
                controller_status   NVARCHAR(50),
                network_status      NVARCHAR(50),
                uptime_seconds      BIGINT,
                uptime_str          NVARCHAR(100),
                last_reboot         NVARCHAR(50),
                reboot_count        INT DEFAULT 0,
                collected_at        NVARCHAR(50)
            )
        """)

        # ------------------------------------------------------------------
        # metrics_history — time-series data for analytics
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='metrics_history' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.metrics_history (
                id                BIGINT IDENTITY(1,1) PRIMARY KEY,
                array_name        NVARCHAR(255) NOT NULL,
                vendor            NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                collected_at      DATETIME2     NOT NULL DEFAULT GETDATE(),
                read_latency_us   FLOAT,
                write_latency_us  FLOAT,
                read_iops         FLOAT,
                write_iops        FLOAT,
                read_bandwidth    BIGINT,
                write_bandwidth   BIGINT,
                capacity_total    BIGINT,
                capacity_used     BIGINT,
                capacity_used_pct FLOAT,
                data_reduction    FLOAT
            )
        """)

        # Migration: widen purity_version to hold ONTAP version strings
        cursor.execute(f"""
            ALTER TABLE {SCHEMA}.metrics_current ALTER COLUMN purity_version NVARCHAR(100)
        """)

        # Migration: add vendor column to metrics_current if missing
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('{SCHEMA}.metrics_current')
                AND name = 'vendor'
            )
            ALTER TABLE {SCHEMA}.metrics_current ADD vendor NVARCHAR(50) NOT NULL DEFAULT 'pure'
        """)

        # Migration: add vendor column to metrics_history if missing
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('{SCHEMA}.metrics_history')
                AND name = 'vendor'
            )
            ALTER TABLE {SCHEMA}.metrics_history ADD vendor NVARCHAR(50) NOT NULL DEFAULT 'pure'
        """)

        # Migration: add vendor column to messages if missing
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('{SCHEMA}.messages')
                AND name = 'vendor'
            )
            ALTER TABLE {SCHEMA}.messages ADD vendor NVARCHAR(50) NOT NULL DEFAULT 'pure'
        """)

        # Migration: add vendor column to volumes_cache if missing
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('{SCHEMA}.volumes_cache')
                AND name = 'vendor'
            )
            ALTER TABLE {SCHEMA}.volumes_cache ADD vendor NVARCHAR(50) NOT NULL DEFAULT 'pure'
        """)

        # Migration: add vendor column to hosts_cache if missing
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('{SCHEMA}.hosts_cache')
                AND name = 'vendor'
            )
            ALTER TABLE {SCHEMA}.hosts_cache ADD vendor NVARCHAR(50) NOT NULL DEFAULT 'pure'
        """)

        # ------------------------------------------------------------------
        # daily_stats — aggregated daily summary across all arrays
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='daily_stats' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.daily_stats (
                id                   INT IDENTITY(1,1) PRIMARY KEY,
                stat_date            DATE    NOT NULL UNIQUE,
                total_arrays         INT,
                total_volumes        INT,
                total_hosts          INT,
                total_capacity_tb    FLOAT,
                total_used_tb        FLOAT,
                avg_utilization_pct  FLOAT,
                avg_data_reduction   FLOAT,
                critical_alerts      INT,
                warning_alerts       INT,
                info_alerts          INT,
                resolved_alerts      INT,
                avg_read_latency_us  FLOAT,
                avg_write_latency_us FLOAT,
                avg_total_iops       FLOAT,
                collected_at         DATETIME2 DEFAULT GETDATE()
            )
        """)

        # ------------------------------------------------------------------
        # volumes_cache — volume inventory (refreshed every ~15 min)
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='volumes_cache' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.volumes_cache (
                id                 INT IDENTITY(1,1) PRIMARY KEY,
                array_name         NVARCHAR(255) NOT NULL,
                vendor             NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                volume_name        NVARCHAR(255) NOT NULL,
                size               BIGINT  DEFAULT 0,
                used               BIGINT  DEFAULT 0,
                data_reduction     FLOAT   DEFAULT 1.0,
                total_reduction    FLOAT   DEFAULT 1.0,
                thin_provisioning  FLOAT   DEFAULT 0,
                snapshots          INT     DEFAULT 0,
                created            NVARCHAR(50),
                serial             NVARCHAR(100),
                hosts              NVARCHAR(MAX),
                host_groups        NVARCHAR(MAX),
                protection_groups  NVARCHAR(MAX),
                notes              NVARCHAR(MAX),
                last_updated       DATETIME2 DEFAULT GETDATE(),
                CONSTRAINT UK_volumes UNIQUE (array_name, volume_name)
            )
        """)

        # ------------------------------------------------------------------
        # hosts_cache — host inventory
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='hosts_cache' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.hosts_cache (
                id           INT IDENTITY(1,1) PRIMARY KEY,
                array_name   NVARCHAR(255) NOT NULL,
                vendor       NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                host_name    NVARCHAR(255) NOT NULL,
                iqn          NVARCHAR(500),
                wwn          NVARCHAR(500),
                nqn          NVARCHAR(500),
                host_group   NVARCHAR(255),
                volumes      NVARCHAR(MAX),
                last_updated DATETIME2 DEFAULT GETDATE(),
                CONSTRAINT UK_hosts UNIQUE (array_name, host_name)
            )
        """)

        # ------------------------------------------------------------------
        # host_groups_cache — host group inventory
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='host_groups_cache' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.host_groups_cache (
                id           INT IDENTITY(1,1) PRIMARY KEY,
                array_name   NVARCHAR(255) NOT NULL,
                vendor       NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                hgroup_name  NVARCHAR(255) NOT NULL,
                hosts        NVARCHAR(MAX),
                volumes      NVARCHAR(MAX),
                last_updated DATETIME2 DEFAULT GETDATE(),
                CONSTRAINT UK_hgroups UNIQUE (array_name, hgroup_name)
            )
        """)

        # ------------------------------------------------------------------
        # protection_groups_cache — replication / protection group inventory
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='protection_groups_cache' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.protection_groups_cache (
                id                  INT IDENTITY(1,1) PRIMARY KEY,
                array_name          NVARCHAR(255) NOT NULL,
                vendor              NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                pgroup_name         NVARCHAR(255) NOT NULL,
                volumes             NVARCHAR(MAX),
                hosts               NVARCHAR(MAX),
                host_groups         NVARCHAR(MAX),
                targets             NVARCHAR(MAX),
                replication_enabled BIT DEFAULT 0,
                last_updated        DATETIME2 DEFAULT GETDATE(),
                CONSTRAINT UK_pgroups UNIQUE (array_name, pgroup_name)
            )
        """)

        # ------------------------------------------------------------------
        # managed_arrays — array inventory managed via Settings UI
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='managed_arrays' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.managed_arrays (
                id             INT IDENTITY(1,1) PRIMARY KEY,
                array_name     NVARCHAR(255) NOT NULL UNIQUE,
                vendor         NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                group_label    NVARCHAR(100),
                enabled        BIT DEFAULT 1,
                created_at     DATETIME2 DEFAULT GETDATE(),
                updated_at     DATETIME2 DEFAULT GETDATE()
            )
        """)

        # Migration: add cred_key column if missing (added for NetApp support)
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.columns
                WHERE object_id = OBJECT_ID('{SCHEMA}.managed_arrays')
                AND name = 'cred_key'
            )
            ALTER TABLE {SCHEMA}.managed_arrays ADD cred_key NVARCHAR(255) NULL
        """)

        # Migration: add DimStorageFinance inventory columns to managed_arrays
        _dim_columns = [
            ("array_fqdn",       "NVARCHAR(255)"),
            ("array_serial",     "NVARCHAR(150)"),
            ("model",            "NVARCHAR(150)"),
            ("site",             "NVARCHAR(50)"),
            ("technology",       "NVARCHAR(50)"),
            ("category",         "NVARCHAR(50)"),
            ("usage_label",      "NVARCHAR(100)"),
            ("disposition",      "NVARCHAR(50)"),
            ("oem",              "NVARCHAR(100)"),
            ("support_provider", "NVARCHAR(100)"),
            ("install_date",     "DATE"),
            ("eosl_date",        "DATE"),
            ("maint_end_date",   "DATE"),
            ("mgmt_ip",          "NVARCHAR(50)"),
            ("dim_sync_at",      "DATETIME2"),
            ("monitoring_status","NVARCHAR(50) DEFAULT 'unknown'"),
        ]
        for col_name, col_type in _dim_columns:
            cursor.execute(f"""
                IF NOT EXISTS (
                    SELECT * FROM sys.columns
                    WHERE object_id = OBJECT_ID('{SCHEMA}.managed_arrays')
                    AND name = '{col_name}'
                )
                ALTER TABLE {SCHEMA}.managed_arrays ADD {col_name} {col_type} NULL
            """)

        # ------------------------------------------------------------------
        # app_settings — key/value store for runtime config (webhook URLs, etc.)
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='app_settings' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.app_settings (
                setting_key    NVARCHAR(255) NOT NULL PRIMARY KEY,
                setting_value  NVARCHAR(MAX),
                updated_at     DATETIME2 DEFAULT GETDATE()
            )
        """)

        # ------------------------------------------------------------------
        # Indexes — only created if missing
        # ------------------------------------------------------------------
        indexes = [
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_messages_array') "
            f"  CREATE INDEX IX_messages_array ON {SCHEMA}.messages(array_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_messages_severity') "
            f"  CREATE INDEX IX_messages_severity ON {SCHEMA}.messages(severity, resolved)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_history_array') "
            f"  CREATE INDEX IX_history_array ON {SCHEMA}.metrics_history(array_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_history_time') "
            f"  CREATE INDEX IX_history_time ON {SCHEMA}.metrics_history(collected_at DESC)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volumes_array') "
            f"  CREATE INDEX IX_volumes_array ON {SCHEMA}.volumes_cache(array_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_hosts_array') "
            f"  CREATE INDEX IX_hosts_array ON {SCHEMA}.hosts_cache(array_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_managed_arrays_vendor') "
            f"  CREATE INDEX IX_managed_arrays_vendor ON {SCHEMA}.managed_arrays(vendor, enabled)",

            # Performance indexes for large tables
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volumes_vendor') "
            f"  CREATE INDEX IX_volumes_vendor ON {SCHEMA}.volumes_cache(vendor)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volumes_array_name') "
            f"  CREATE INDEX IX_volumes_array_name ON {SCHEMA}.volumes_cache(array_name, volume_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_hosts_vendor') "
            f"  CREATE INDEX IX_hosts_vendor ON {SCHEMA}.hosts_cache(vendor)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_hosts_name') "
            f"  CREATE INDEX IX_hosts_name ON {SCHEMA}.hosts_cache(host_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_metrics_current_array') "
            f"  CREATE INDEX IX_metrics_current_array ON {SCHEMA}.metrics_current(array_name)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_messages_resolved') "
            f"  CREATE INDEX IX_messages_resolved ON {SCHEMA}.messages(resolved, array_name)",
        ]
        for idx_sql in indexes:
            try:
                cursor.execute(idx_sql)
            except Exception as e:
                logger.warning(f"Index creation skipped: {e}")

    logger.info("Database schema initialization complete.")

    # Seed managed_arrays from arrays.txt if table is empty (uses its own connection)
    _migrate_arrays_txt_to_db()


def _migrate_arrays_txt_to_db() -> None:
    """One-time import: if managed_arrays is empty AND arrays.txt exists, seed from file."""
    import os
    path = settings.arrays_config_file
    if not os.path.exists(path):
        return

    with get_db_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.managed_arrays")
        count = cur.fetchone()[0]
        if count > 0:
            return  # already populated

        inserted = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                name = parts[0]
                vendor = parts[1] if len(parts) > 1 else "pure"
                group = parts[2] if len(parts) > 2 else None
                cur.execute(
                    f"INSERT INTO {SCHEMA}.managed_arrays (array_name, vendor, group_label) VALUES (?, ?, ?)",
                    (name, vendor, group),
                )
                inserted += 1

        if inserted:
            logger.info(f"Migrated {inserted} arrays from arrays.txt into managed_arrays table")
