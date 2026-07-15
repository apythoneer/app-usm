"""
SQLite read cache — local fast-read mirror of SQL Server hot tables.

Phase 1: metrics_current only.
Collectors dual-write: SQL Server (primary) + SQLite (fast read cache).
API reads from SQLite for sub-ms latency.
"""

import os
import sqlite3
import threading
import logging
from typing import Optional, List, Dict, Any, Generator
from contextlib import contextmanager

logger = logging.getLogger("usm.cache")

# SQLite DB path — inside Docker container, mounted as volume
CACHE_DB_PATH = os.environ.get("USM_CACHE_DB", "/app/data/usm_cache.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get thread-local SQLite connection with WAL mode."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8192")  # 8MB cache
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


@contextmanager
def get_cache_cursor() -> Generator:
    """Context manager for SQLite cursor with auto-commit."""
    conn = _get_conn()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_cache() -> None:
    """Create SQLite cache tables if they don't exist."""
    logger.info(f"Initializing SQLite cache at {CACHE_DB_PATH}")
    with get_cache_cursor() as cur:
        # metrics_current mirror
        cur.execute("""
            CREATE TABLE IF NOT EXISTS metrics_current (
                array_name      TEXT PRIMARY KEY,
                vendor          TEXT NOT NULL DEFAULT 'pure',
                purity_version  TEXT,
                read_latency_us INTEGER,
                write_latency_us INTEGER,
                read_iops       INTEGER,
                write_iops      INTEGER,
                read_bandwidth  INTEGER,
                write_bandwidth INTEGER,
                capacity_total  INTEGER,
                capacity_used   INTEGER,
                capacity_used_pct REAL,
                data_reduction  REAL,
                total_reduction REAL,
                shared_space    INTEGER,
                snapshot_space  INTEGER,
                volume_space    INTEGER,
                array_status    TEXT,
                controller_status TEXT,
                network_status  TEXT,
                uptime_seconds  INTEGER,
                uptime_str      TEXT,
                last_reboot     TEXT,
                reboot_count    INTEGER DEFAULT 0,
                collected_at    TEXT
            )
        """)

        # volumes_cache mirror
        cur.execute("""
            CREATE TABLE IF NOT EXISTS volumes_cache (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                array_name      TEXT NOT NULL,
                vendor          TEXT NOT NULL DEFAULT 'pure',
                volume_name     TEXT NOT NULL,
                size            INTEGER,
                used            INTEGER,
                data_reduction  REAL,
                total_reduction REAL,
                thin_provisioning REAL,
                snapshots       INTEGER,
                created         TEXT,
                serial          TEXT,
                hosts           TEXT,
                host_groups     TEXT,
                protection_groups TEXT,
                notes           TEXT,
                last_updated    TEXT
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vol_array_name
            ON volumes_cache(array_name, volume_name)
        """)

        # hosts_cache mirror
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hosts_cache (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                array_name      TEXT NOT NULL,
                vendor          TEXT NOT NULL DEFAULT 'pure',
                host_name       TEXT NOT NULL,
                wwn             TEXT,
                iqn             TEXT,
                nqn             TEXT,
                host_group      TEXT,
                volumes         TEXT,
                last_updated    TEXT
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_host_array_name
            ON hosts_cache(array_name, host_name)
        """)

        # messages mirror (alerts)
        #
        # ⚠️ NOT CURRENTLY MIRRORED — this table is created but never written.
        # base.py `_write_to_cache()` only handles COLLECTOR_TYPE "metrics" and
        # "volumes"; there is no "alerts" branch. Do not read alert state from
        # here — it is always empty, which previously caused the dashboard to
        # report 0 active alerts fleet-wide.
        #
        # Populating it needs more than an alerts branch in `_write_to_cache`:
        # alert state is also mutated directly in SQL Server by
        # services/capacity_alerts.py, the alert_cleanup job, and netapp/alerts.py
        # auto-resolve. All of those would need matching mirror writes, or the
        # mirror will silently drift.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY,
                array_name      TEXT NOT NULL,
                vendor          TEXT NOT NULL DEFAULT 'pure',
                message_id      INTEGER NOT NULL,
                event           TEXT,
                severity        TEXT,
                component_type  TEXT,
                component_name  TEXT,
                opened          TEXT,
                closed          TEXT,
                expected        TEXT,
                actual          TEXT,
                collected_at    TEXT,
                alerted         TEXT,
                teams_notified  TEXT,
                snow_ticket     TEXT,
                suppressed      INTEGER DEFAULT 0,
                resolved        INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_array_msgid
            ON messages(array_name, message_id)
        """)

    logger.info("SQLite cache initialized")


# ── Convenience helpers ───────────────────────────────────────────────────────

def upsert_metrics(data: Dict[str, Any]) -> None:
    """Upsert a single metrics_current row into SQLite cache."""
    try:
        with get_cache_cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO metrics_current (
                    array_name, vendor, purity_version,
                    read_latency_us, write_latency_us,
                    read_iops, write_iops,
                    read_bandwidth, write_bandwidth,
                    capacity_total, capacity_used, capacity_used_pct,
                    data_reduction, total_reduction,
                    shared_space, snapshot_space, volume_space,
                    array_status, controller_status, network_status,
                    uptime_seconds, uptime_str, last_reboot, reboot_count,
                    collected_at
                ) VALUES (
                    :array_name, :vendor, :purity_version,
                    :read_latency_us, :write_latency_us,
                    :read_iops, :write_iops,
                    :read_bandwidth, :write_bandwidth,
                    :capacity_total, :capacity_used, :capacity_used_pct,
                    :data_reduction, :total_reduction,
                    :shared_space, :snapshot_space, :volume_space,
                    :array_status, :controller_status, :network_status,
                    :uptime_seconds, :uptime_str, :last_reboot, :reboot_count,
                    :collected_at
                )
            """, data)
    except Exception as e:
        logger.warning(f"SQLite cache upsert_metrics failed: {e}")


def fetch_all_metrics() -> List[Dict[str, Any]]:
    """Fetch all metrics_current rows from SQLite cache."""
    try:
        with get_cache_cursor() as cur:
            cur.execute("SELECT * FROM metrics_current ORDER BY array_name")
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.warning(f"SQLite cache fetch_all_metrics failed: {e}")
        return []


def fetch_metrics(array_name: str) -> Optional[Dict[str, Any]]:
    """Fetch a single array's metrics from SQLite cache."""
    try:
        with get_cache_cursor() as cur:
            cur.execute("SELECT * FROM metrics_current WHERE array_name=?", (array_name,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.warning(f"SQLite cache fetch_metrics failed: {e}")
        return None


# NOTE: fetch_fleet_stats() was removed deliberately — do not reintroduce it.
#
# It computed fleet totals from this cache, including
#   SELECT COUNT(*) FROM messages WHERE resolved=0 AND suppressed=0
# but nothing ever writes the `messages` table (see the table comment above), so
# it always reported 0 active alerts. /arrays/fleet-stats called it first and
# only fell back to SQL Server when total_arrays was 0, so the dashboard showed
# 0 active alerts fleet-wide from 2026-05-15 until this was removed.
#
# Fleet stats are now served from SQL Server, which is the source of truth for
# current state. If this cache ever becomes a read path again, it must first
# mirror alert state — including the resolve/purge paths that currently write
# only to SQL Server (services/capacity_alerts.py, the alert_cleanup job, and
# netapp/alerts.py auto-resolve).


def cache_stats() -> Dict[str, int]:
    """Return row counts for each cached table."""
    try:
        with get_cache_cursor() as cur:
            stats = {}
            for table in ["metrics_current", "volumes_cache", "hosts_cache", "messages"]:
                cur.execute(f"SELECT COUNT(*) as c FROM {table}")
                stats[table] = cur.fetchone()["c"]
            return stats
    except Exception as e:
        logger.warning(f"SQLite cache stats failed: {e}")
        return {}
