"""
Daily statistics service.
Aggregates cross-array metrics into the daily_stats table.
Called by the scheduler once per day (and on demand).
Ported from v1 collectors/pure/metrics.py::calculate_daily_stats().
"""

import logging
from datetime import datetime

from app.db.session import get_db_cursor
from app.core.config import get_settings

logger = logging.getLogger("usm.stats")
settings = get_settings()
SCHEMA = settings.db_schema


def calculate_daily_stats() -> bool:
    """
    Aggregate today's metrics into daily_stats.
    Safe to call multiple times — deletes and rewrites today's row.
    """
    stat_date = datetime.now().date()
    logger.info(f"Calculating daily stats for {stat_date}...")

    try:
        with get_db_cursor() as cursor:

            # Delete existing row for today (idempotent upsert)
            cursor.execute(
                f"DELETE FROM {SCHEMA}.daily_stats WHERE stat_date = ?",
                (stat_date,),
            )

            # Array count
            cursor.execute(
                f"SELECT COUNT(DISTINCT array_name) FROM {SCHEMA}.metrics_current"
            )
            total_arrays = cursor.fetchone()[0] or 0

            # Volume + host counts
            cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.volumes_cache")
            total_volumes = cursor.fetchone()[0] or 0

            cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.hosts_cache")
            total_hosts = cursor.fetchone()[0] or 0

            # Capacity (convert bytes → TB)
            cursor.execute(f"""
                SELECT
                    SUM(CAST(capacity_total AS FLOAT)) / 1099511627776.0,
                    SUM(CAST(capacity_used  AS FLOAT)) / 1099511627776.0,
                    AVG(capacity_used_pct),
                    AVG(data_reduction)
                FROM {SCHEMA}.metrics_current
            """)
            cap = cursor.fetchone()
            total_cap_tb  = cap[0] or 0.0
            total_used_tb = cap[1] or 0.0
            avg_util_pct  = cap[2] or 0.0
            avg_dr        = cap[3] or 1.0

            # Alert counts
            cursor.execute(f"""
                SELECT
                    SUM(CASE WHEN severity = 'critical' AND resolved = 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN severity = 'warning'  AND resolved = 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN severity NOT IN ('critical','warning') AND resolved = 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END)
                FROM {SCHEMA}.messages
            """)
            alerts = cursor.fetchone()
            critical_alerts  = alerts[0] or 0
            warning_alerts   = alerts[1] or 0
            info_alerts      = alerts[2] or 0
            resolved_alerts  = alerts[3] or 0

            # Performance averages
            cursor.execute(f"""
                SELECT
                    AVG(CAST(read_latency_us  AS FLOAT)),
                    AVG(CAST(write_latency_us AS FLOAT)),
                    AVG(CAST(read_iops        AS FLOAT) + CAST(write_iops AS FLOAT))
                FROM {SCHEMA}.metrics_current
            """)
            perf = cursor.fetchone()
            avg_read_lat  = perf[0] or 0.0
            avg_write_lat = perf[1] or 0.0
            avg_iops      = perf[2] or 0.0

            cursor.execute(f"""
                INSERT INTO {SCHEMA}.daily_stats (
                    stat_date, total_arrays, total_volumes, total_hosts,
                    total_capacity_tb, total_used_tb, avg_utilization_pct, avg_data_reduction,
                    critical_alerts, warning_alerts, info_alerts, resolved_alerts,
                    avg_read_latency_us, avg_write_latency_us, avg_total_iops
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stat_date,
                total_arrays, total_volumes, total_hosts,
                total_cap_tb, total_used_tb, avg_util_pct, avg_dr,
                critical_alerts, warning_alerts, info_alerts, resolved_alerts,
                avg_read_lat, avg_write_lat, avg_iops,
            ))

        logger.info(
            f"Daily stats saved: {total_arrays} arrays, "
            f"{total_cap_tb:.1f} TB total, {total_used_tb:.1f} TB used, "
            f"{critical_alerts} critical alerts"
        )
        return True

    except Exception as e:
        logger.error(f"Daily stats calculation failed: {e}", exc_info=True)
        return False


def cleanup_old_history(days: int = None) -> int:
    """
    Remove metrics_history AND volumes_history rows older than `days` days.
    Defaults to settings.history_retention_days (365) so YTD capacity-growth
    analytics have enough history. Keeps the tables from growing unbounded.
    Returns total number of rows deleted across both tables.

    volumes_history was previously not cleaned up at all — only metrics_history
    was — so it grew unbounded from the day it shipped (2026-06-19). Deleted in
    batches because it is the far larger table and an unbatched DELETE would hold
    a long lock and bloat the transaction log.
    """
    if days is None:
        days = settings.history_retention_days
    deleted = 0
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {SCHEMA}.metrics_history "
                f"WHERE collected_at < DATEADD(DAY, -?, GETDATE())",
                (days,),
            )
            deleted = cursor.rowcount or 0
        logger.info(f"Cleanup: removed {deleted} metrics_history rows older than {days} days")
    except Exception as e:
        logger.error(f"metrics_history cleanup failed: {e}")

    # volumes_history uses its OWN, shorter window. It holds ~44k rows per
    # collection (one per volume) vs metrics_history's ~90 (one per array), so
    # reusing the 365d metrics window would mean ~700M rows. See
    # settings.volume_history_retention_days for the arithmetic.
    vol_days = settings.volume_history_retention_days
    vol_deleted = 0
    try:
        # Batched: keeps each transaction short so collectors are not blocked and
        # the log can be reused between batches. The first run after enabling this
        # may delete tens of millions of rows.
        while True:
            with get_db_cursor() as cursor:
                cursor.execute(
                    f"DELETE TOP (50000) FROM {SCHEMA}.volumes_history "
                    f"WHERE collected_at < DATEADD(DAY, -?, GETDATE())",
                    (vol_days,),
                )
                n = cursor.rowcount or 0
            vol_deleted += n
            if n < 50000:
                break
        logger.info(f"Cleanup: removed {vol_deleted} volumes_history rows older than {vol_days} days")
    except Exception as e:
        logger.error(f"volumes_history cleanup failed: {e}")

    return deleted + vol_deleted
