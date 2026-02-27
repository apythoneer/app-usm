"""
Analytics API — time-series metrics history.
"""

from typing import List, Optional
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from app.db.session import get_db_cursor, rows_to_dicts
from app.core.config import get_settings

router = APIRouter(prefix="/analytics", tags=["analytics"])
settings = get_settings()
SCHEMA = settings.db_schema


def _fetch_history(array_name: str, hours: int, limit: int) -> List[dict]:
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT TOP {limit}
                array_name, collected_at,
                read_iops, write_iops,
                read_latency_us, write_latency_us,
                read_bandwidth, write_bandwidth,
                capacity_total, capacity_used, capacity_used_pct, data_reduction
            FROM {SCHEMA}.metrics_history
            WHERE array_name=?
              AND collected_at >= DATEADD(HOUR, -?, GETDATE())
            ORDER BY collected_at ASC""",
            (array_name, hours),
        )
        return rows_to_dicts(cursor, cursor.fetchall())


def _fetch_daily_stats(days: int) -> List[dict]:
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT TOP {days} * FROM {SCHEMA}.daily_stats
            ORDER BY stat_date DESC""",
        )
        return rows_to_dicts(cursor, cursor.fetchall())


@router.get("/history/{array_name}")
async def get_array_history(
    array_name: str,
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=1440, le=10000),
):
    """Time-series metrics for a single array (default last 24h)."""
    rows = await run_in_threadpool(_fetch_history, array_name, hours, limit)
    return {"array_name": array_name, "hours": hours, "data_points": len(rows), "data": rows}


@router.get("/daily")
async def get_daily_stats(days: int = Query(default=30, ge=1, le=365)):
    """Daily aggregated stats across all arrays."""
    rows = await run_in_threadpool(_fetch_daily_stats, days)
    return {"days": days, "data": rows}


@router.get("/fleet-history")
async def get_fleet_history(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=5000, le=20000),
):
    """Time-series metrics for ALL arrays in a single query."""
    rows = await run_in_threadpool(_fetch_fleet_history, hours, limit)
    arrays = list({r["array_name"] for r in rows})
    return {"hours": hours, "arrays": arrays, "data_points": len(rows), "data": rows}


def _fetch_fleet_history(hours: int, limit: int) -> List[dict]:
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT TOP {limit}
                array_name, collected_at,
                read_iops, write_iops,
                read_latency_us, write_latency_us,
                capacity_used_pct
            FROM {SCHEMA}.metrics_history
            WHERE collected_at >= DATEADD(HOUR, -?, GETDATE())
            ORDER BY collected_at ASC""",
            (hours,),
        )
        return rows_to_dicts(cursor, cursor.fetchall())
