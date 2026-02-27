"""
Arrays API — current metrics for all storage arrays (any vendor).
"""

import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.db.session import get_db_cursor, rows_to_dicts, row_to_dict
from app.schemas.array import ArrayMetrics, ArraySummary
from app.core.config import get_settings

router = APIRouter(prefix="/arrays", tags=["arrays"])
settings = get_settings()
SCHEMA = settings.db_schema


def _load_group_map() -> dict:
    """Read arrays.txt and return {array_name: group} from column 3."""
    path = settings.arrays_config_file
    result = {}
    if not os.path.exists(path):
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                result[parts[0]] = parts[2]
    return result


def _fetch_arrays(vendor: Optional[str] = None) -> List[dict]:
    with get_db_cursor() as cursor:
        if vendor:
            cursor.execute(
                f"SELECT * FROM {SCHEMA}.metrics_current WHERE vendor=? ORDER BY array_name",
                (vendor,),
            )
        else:
            cursor.execute(
                f"SELECT * FROM {SCHEMA}.metrics_current ORDER BY array_name"
            )
        return rows_to_dicts(cursor, cursor.fetchall())


def _fetch_array(array_name: str) -> Optional[dict]:
    with get_db_cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM {SCHEMA}.metrics_current WHERE array_name=?",
            (array_name,),
        )
        return row_to_dict(cursor, cursor.fetchone())


def _fetch_fleet_stats() -> dict:
    with get_db_cursor() as cursor:
        # Core metrics from current snapshot
        cursor.execute(f"""
            SELECT
                COUNT(*) AS total_arrays,
                SUM(CAST(capacity_total AS FLOAT)) / 1099511627776.0 AS total_capacity_tb,
                SUM(CAST(capacity_used  AS FLOAT)) / 1099511627776.0 AS total_used_tb,
                AVG(capacity_used_pct)  AS avg_utilization_pct,
                SUM(read_iops + write_iops) AS total_iops,
                AVG(read_latency_us)    AS avg_read_latency_us,
                AVG(write_latency_us)   AS avg_write_latency_us,
                AVG(data_reduction)     AS avg_data_reduction
            FROM {SCHEMA}.metrics_current
        """)
        row = dict(zip([c[0] for c in cursor.description], cursor.fetchone() or []))

        # Active alerts
        cursor.execute(
            f"SELECT COUNT(*) FROM {SCHEMA}.messages WHERE resolved=0 AND suppressed=0"
        )
        row["active_alerts"] = cursor.fetchone()[0]

        # Total volumes
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.volumes_cache")
        row["total_volumes"] = cursor.fetchone()[0]

        # Total hosts
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.hosts_cache")
        row["total_hosts"] = cursor.fetchone()[0]

    return row


@router.get("/fleet-stats")
async def get_fleet_stats():
    """Aggregate metrics across all arrays for the fleet stats dashboard row."""
    return await run_in_threadpool(_fetch_fleet_stats)


@router.get("", response_model=List[ArraySummary])
async def list_arrays(vendor: Optional[str] = Query(default=None)):
    """List all arrays with summary metrics. Filter by ?vendor=pure|netapp|..."""
    rows = await run_in_threadpool(_fetch_arrays, vendor)
    group_map = _load_group_map()
    return [_row_to_summary(r, group_map) for r in rows]


@router.get("/{array_name}", response_model=ArrayMetrics)
async def get_array(array_name: str):
    """Get full metrics for a single array."""
    row = await run_in_threadpool(_fetch_array, array_name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Array '{array_name}' not found")
    group_map = _load_group_map()
    return _row_to_metrics(row, group_map)


# ------------------------------------------------------------------ helpers

def _row_to_summary(row: dict, group_map: dict) -> ArraySummary:
    name = row.get("array_name", "")
    return ArraySummary(
        array_name=name,
        vendor=row.get("vendor", "pure"),
        model=row.get("purity_version"),
        group=group_map.get(name),
        capacity_total_bytes=row.get("capacity_total"),
        capacity_used_pct=row.get("capacity_used_pct"),
        data_reduction=row.get("data_reduction"),
        total_iops=(row.get("read_iops") or 0) + (row.get("write_iops") or 0),
        read_latency_us=row.get("read_latency_us"),
        write_latency_us=row.get("write_latency_us"),
        array_status=row.get("array_status") or row.get("controller_status"),
        collected_at=row.get("collected_at"),
    )


def _row_to_metrics(row: dict, group_map: dict) -> ArrayMetrics:
    read_iops = row.get("read_iops") or 0
    write_iops = row.get("write_iops") or 0
    name = row.get("array_name", "")
    return ArrayMetrics(
        array_name=name,
        vendor=row.get("vendor", "pure"),
        firmware_version=row.get("purity_version"),
        read_iops=read_iops,
        write_iops=write_iops,
        total_iops=read_iops + write_iops,
        read_latency_us=row.get("read_latency_us"),
        write_latency_us=row.get("write_latency_us"),
        read_bandwidth_bytes=row.get("read_bandwidth"),
        write_bandwidth_bytes=row.get("write_bandwidth"),
        capacity_total_bytes=row.get("capacity_total"),
        capacity_used_bytes=row.get("capacity_used"),
        capacity_used_pct=row.get("capacity_used_pct"),
        data_reduction=row.get("data_reduction"),
        total_reduction=row.get("total_reduction"),
        shared_space_bytes=row.get("shared_space"),
        snapshot_space_bytes=row.get("snapshot_space"),
        volume_space_bytes=row.get("volume_space"),
        array_status=row.get("array_status"),
        controller_status=row.get("controller_status"),
        uptime_seconds=row.get("uptime_seconds"),
        uptime_str=row.get("uptime_str"),
        collected_at=row.get("collected_at"),
        metadata={
            "purity_version": row.get("purity_version", ""),
            "group": group_map.get(name),
        },
    )
