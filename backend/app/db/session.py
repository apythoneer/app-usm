"""
Database session management — pyodbc connection pool for SQL Server.
FastAPI uses run_in_threadpool to keep async handlers non-blocking.
"""

import logging
import pyodbc
from contextlib import contextmanager
from typing import Generator, Optional

from app.core.config import get_settings
from app.services.keepass import get_sql_credentials

logger = logging.getLogger("usm.db")
settings = get_settings()
SCHEMA = settings.db_schema


def _build_conn_str(username: str, password: str) -> str:
    preferred = [settings.sql_driver, "ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
    available = pyodbc.drivers()
    driver = next((d for d in preferred if d in available), preferred[-1])
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={settings.sql_server};"
        f"DATABASE={settings.sql_database};"
        f"UID={username};PWD={password};"
        "TrustServerCertificate=yes;Connection Timeout=30;"
    )


def get_connection() -> pyodbc.Connection:
    """Get a new pyodbc connection (credentials from KeePass)."""
    creds = get_sql_credentials()
    conn_str = _build_conn_str(creds["username"], creds["password"])
    conn = pyodbc.connect(conn_str, autocommit=False)
    conn.timeout = 30
    return conn


@contextmanager
def get_db_cursor() -> Generator:
    """
    Context manager yielding a cursor with auto commit/rollback.
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
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


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
        ]
        for idx_sql in indexes:
            try:
                cursor.execute(idx_sql)
            except Exception as e:
                logger.warning(f"Index creation skipped: {e}")

    logger.info("Database schema initialization complete.")
