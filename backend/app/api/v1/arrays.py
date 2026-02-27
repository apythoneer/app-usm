"""
Arrays API — current metrics for all storage arrays (any vendor).
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.db.session import get_db_cursor, rows_to_dicts, row_to_dict
from app.schemas.array import ArrayMetrics, ArraySummary
from app.core.config import get_settings

router = APIRouter(prefix="/arrays", tags=["arrays"])
settings = get_settings()
SCHEMA = settings.db_schema


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


@router.get("", response_model=List[ArraySummary])
async def list_arrays(vendor: Optional[str] = Query(default=None)):
    """List all arrays with summary metrics. Filter by ?vendor=pure|netapp|..."""
    rows = await run_in_threadpool(_fetch_arrays, vendor)
    return [_row_to_summary(r) for r in rows]


@router.get("/{array_name}", response_model=ArrayMetrics)
async def get_array(array_name: str):
    """Get full metrics for a single array."""
    row = await run_in_threadpool(_fetch_array, array_name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Array '{array_name}' not found")
    return _row_to_metrics(row)


# ------------------------------------------------------------------ helpers

def _row_to_summary(row: dict) -> ArraySummary:
    return ArraySummary(
        array_name=row.get("array_name", ""),
        vendor=row.get("vendor", "pure"),
        model=row.get("purity_version"),
        capacity_used_pct=row.get("capacity_used_pct"),
        total_iops=(row.get("read_iops") or 0) + (row.get("write_iops") or 0),
        read_latency_us=row.get("read_latency_us"),
        write_latency_us=row.get("write_latency_us"),
        array_status=row.get("array_status") or row.get("controller_status"),
        collected_at=row.get("collected_at"),
    )


def _row_to_metrics(row: dict) -> ArrayMetrics:
    read_iops = row.get("read_iops") or 0
    write_iops = row.get("write_iops") or 0
    return ArrayMetrics(
        array_name=row.get("array_name", ""),
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
        metadata={"purity_version": row.get("purity_version", "")},
    )
