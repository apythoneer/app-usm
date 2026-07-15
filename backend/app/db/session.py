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


@contextmanager
def get_fast_cursor() -> Generator:
    """
    Like get_db_cursor() but enables pyodbc fast_executemany on the cursor.

    Use for bulk INSERT/UPDATE via cursor.executemany(...). Dramatically reduces
    round-trips to SQL Server (10-50x) for large batches like volume inventory.
    """
    conn: Optional[pyodbc.Connection] = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.fast_executemany = True
        except Exception:
            pass  # driver may not support it; executemany still works, just slower
        yield cursor
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def batch_upsert(
    cursor,
    table: str,
    key_cols: tuple,
    update_cols: tuple,
    rows: list,
    *,
    array_name: str,
    delete_missing: bool = True,
    extra_where: str = "",
    extra_params: tuple = (),
    batch_size: int = 1000,
) -> int:
    """
    Set-based upsert for cache tables (volumes_cache, hosts_cache, etc).

    Strategy (avoids the per-row SELECT-then-INSERT/UPDATE N+1 antipattern):
      1. ONE SELECT to load existing key values for this array.
      2. Partition incoming rows into inserts vs updates in memory.
      3. Bulk DELETE rows that no longer exist (optional, chunked).
      4. Bulk INSERT new rows via executemany (fast_executemany).
      5. Bulk UPDATE changed rows via executemany.

    Args:
        cursor:        a cursor from get_fast_cursor().
        table:         fully-qualified table name (e.g. f"{SCHEMA}.volumes_cache").
        key_cols:      columns that uniquely identify a row WITHIN the array
                       (e.g. ("volume_name",)). array_name is always included.
        update_cols:   non-key columns to insert/update (in stable order).
        rows:          list of dicts, each containing key_cols + update_cols values.
        array_name:    the array these rows belong to.
        delete_missing: if True, delete DB rows whose key isn't in `rows`.
        extra_where:   extra SQL appended to the existing-key SELECT / DELETE
                       (e.g. " AND vendor='netapp'"). Must be safe/static.
        extra_params:  params for extra_where placeholders (if any).
        batch_size:    chunk size for executemany / delete batches.

    Returns:
        number of rows written (inserts + updates).
    """
    key_tuple = lambda r: tuple(r[k] for k in key_cols)

    # 1. existing keys for this array
    key_select = ", ".join(key_cols)
    cursor.execute(
        f"SELECT {key_select} FROM {table} WHERE array_name=?{extra_where}",
        (array_name, *extra_params),
    )
    existing = {tuple(row) for row in cursor.fetchall()}
    incoming = {key_tuple(r): r for r in rows}

    # 3. delete rows no longer present
    if delete_missing:
        stale = existing - set(incoming)
        if stale:
            key_pred = " AND ".join(f"{k}=?" for k in key_cols)
            del_sql = f"DELETE FROM {table} WHERE array_name=? AND {key_pred}"
            stale_list = list(stale)
            for i in range(0, len(stale_list), batch_size):
                chunk = stale_list[i:i + batch_size]
                cursor.executemany(del_sql, [(array_name, *k) for k in chunk])

    # 2. partition
    to_insert = []
    to_update = []
    for k, r in incoming.items():
        if k in existing:
            to_update.append(r)
        else:
            to_insert.append(r)

    written = 0

    # 4. bulk INSERT (array_name + key_cols + update_cols)
    if to_insert:
        insert_cols = ("array_name", *key_cols, *update_cols)
        placeholders = ",".join("?" for _ in insert_cols)
        ins_sql = f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({placeholders})"
        params = [
            (array_name, *[r[k] for k in key_cols], *[r[c] for c in update_cols])
            for r in to_insert
        ]
        for i in range(0, len(params), batch_size):
            cursor.executemany(ins_sql, params[i:i + batch_size])
        written += len(params)

    # 5. bulk UPDATE (set update_cols + last_updated, key on array_name + key_cols)
    if to_update:
        set_clause = ", ".join(f"{c}=?" for c in update_cols) + ", last_updated=GETDATE()"
        key_pred = " AND ".join(f"{k}=?" for k in key_cols)
        upd_sql = f"UPDATE {table} SET {set_clause} WHERE array_name=? AND {key_pred}"
        params = [
            (*[r[c] for c in update_cols], array_name, *[r[k] for k in key_cols])
            for r in to_update
        ]
        for i in range(0, len(params), batch_size):
            cursor.executemany(upd_sql, params[i:i + batch_size])
        written += len(params)

    return written


def snapshot_volume_history(cursor, array_name: str) -> int:
    """
    Append a timestamped point-in-time snapshot of an array's volumes into
    volumes_history, copied straight from volumes_cache.

    Call this AFTER the volumes_cache upsert (within the same cursor /
    transaction) so the history row reflects exactly what was just persisted.

    One set-based INSERT...SELECT — no per-volume round trips. The collected_at
    column defaults to GETDATE() so every volume in this batch shares the same
    timestamp, which keeps per-collection grouping clean for trend queries.

    Returns the number of history rows written (== current volume count).
    """
    cursor.execute(
        f"""
        INSERT INTO {SCHEMA}.volumes_history
            (array_name, vendor, volume_name, size, used,
             data_reduction, total_reduction, snapshots, collected_at)
        SELECT
            array_name, vendor, volume_name, size, used,
            data_reduction, total_reduction, snapshots, GETDATE()
        FROM {SCHEMA}.volumes_cache
        WHERE array_name = ?
        """,
        (array_name,),
    )
    try:
        return cursor.rowcount if cursor.rowcount is not None else 0
    except Exception:
        return 0


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
        # volumes_history — per-volume time-series (append-only)
        # One row per volume per collection. Powers volume growth/decline
        # analytics (mirrors metrics_history but at volume granularity).
        # ------------------------------------------------------------------
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT * FROM sys.tables
                WHERE name='volumes_history' AND schema_id = SCHEMA_ID('{SCHEMA}')
            )
            CREATE TABLE {SCHEMA}.volumes_history (
                id                 BIGINT IDENTITY(1,1) PRIMARY KEY,
                array_name         NVARCHAR(255) NOT NULL,
                vendor             NVARCHAR(50)  NOT NULL DEFAULT 'pure',
                volume_name        NVARCHAR(255) NOT NULL,
                collected_at       DATETIME2     NOT NULL DEFAULT GETDATE(),
                size               BIGINT  DEFAULT 0,
                used               BIGINT  DEFAULT 0,
                data_reduction     FLOAT   DEFAULT 1.0,
                total_reduction    FLOAT   DEFAULT 1.0,
                -- BIGINT, not INT: `snapshots` is snapshot space in BYTES (not a
                -- count — that's snap_count), so it routinely exceeds 2^31. It must
                -- match volumes_cache.snapshots, which snapshot_volume_history()
                -- copies from via INSERT...SELECT. See the migration below.
                snapshots          BIGINT  DEFAULT 0
            )
        """)

        # Migration: widen volumes_history.snapshots INT -> BIGINT.
        #
        # This column shipped as INT in f1cfbcb while volumes_cache.snapshots is
        # BIGINT. snapshot_volume_history() does
        #     INSERT INTO volumes_history (... snapshots ...)
        #     SELECT ... snapshots ... FROM volumes_cache
        # so any volume with >2GiB of snapshot space made SQL Server fail the
        # server-side conversion with:
        #     22003 Arithmetic overflow error converting expression to data type int
        # That aborted the whole transaction — including the volumes_cache upsert
        # that ran just before it in the same cursor — so the array's ENTIRE volume
        # inventory silently failed to save.
        #
        # Only Pure was affected: it is the sole vendor reporting non-zero
        # snapshots (max ~1.45e12 across 311 volumes); every other vendor reports 0
        # and so never crossed the INT boundary. It ran ~912x/day from 2026-06-19
        # (the day volumes_history shipped) until 2026-07-15.
        #
        # Unconditional ALTER, matching the purity_version migration above: it is a
        # no-op when the column is already BIGINT.
        cursor.execute(f"""
            ALTER TABLE {SCHEMA}.volumes_history ALTER COLUMN snapshots BIGINT
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

            # volumes_history — supports per-array / per-volume trailing-window scans
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volhist_array_time') "
            f"  CREATE INDEX IX_volhist_array_time ON {SCHEMA}.volumes_history(array_name, collected_at DESC)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volhist_vol') "
            f"  CREATE INDEX IX_volhist_vol ON {SCHEMA}.volumes_history(array_name, volume_name, collected_at DESC)",

            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volhist_time') "
            f"  CREATE INDEX IX_volhist_time ON {SCHEMA}.volumes_history(collected_at DESC)",


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
