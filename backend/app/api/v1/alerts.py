"""
Alerts API — unified alert feed across all vendors.
"""

from typing import List, Optional
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from app.db.session import get_db_cursor, rows_to_dicts
from app.schemas.alert import AlertSchema
from app.core.config import get_settings

router = APIRouter(prefix="/alerts", tags=["alerts"])
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SORT_COLS = {
    "id", "array_name", "vendor", "severity", "event",
    "component_name", "opened", "closed",
}


def _fetch_alerts(
    array_name: Optional[str],
    severity: Optional[str],
    vendor: Optional[str],
    resolved: Optional[bool],
    suppressed: bool,
    limit: int,
    offset: int,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
) -> dict:
    where = ["suppressed = ?"]
    params: list = [1 if suppressed else 0]

    if array_name:
        where.append("array_name = ?")
        params.append(array_name)
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if vendor:
        where.append("vendor = ?")
        params.append(vendor)
    if resolved is not None:
        where.append("resolved = ?")
        params.append(1 if resolved else 0)

    where_clause = " WHERE " + " AND ".join(where)

    # Sorting
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    if sort_by and sort_by in ALERT_SORT_COLS:
        order = f"{sort_by} {direction}, id DESC"
    else:
        order = "id DESC"

    # Total count
    with get_db_cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.messages WITH (NOLOCK){where_clause}", params)
        total = cursor.fetchone()[0]

    # Paginated data
    sql = (
        f"SELECT * FROM {SCHEMA}.messages WITH (NOLOCK){where_clause} "
        f"ORDER BY {order} OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    )
    with get_db_cursor() as cursor:
        cursor.execute(sql, params + [offset, limit])
        rows = rows_to_dicts(cursor, cursor.fetchall())

    return {"total": total, "rows": rows}


@router.get("")
async def list_alerts(
    array_name: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    vendor: Optional[str] = Query(default=None),
    resolved: Optional[bool] = Query(default=None),
    suppressed: bool = Query(default=False),
    limit: int = Query(default=50, le=1000),
    offset: int = Query(default=0, ge=0),
    sort_by: Optional[str] = Query(default=None),
    sort_dir: str = Query(default="desc"),
):
    """List alerts with pagination, filtering, and sorting."""
    result = await run_in_threadpool(
        _fetch_alerts, array_name, severity, vendor, resolved, suppressed,
        limit, offset, sort_by, sort_dir,
    )
    return {
        "total": result["total"],
        "limit": limit,
        "offset": offset,
        "data": [_row_to_alert(r) for r in result["rows"]],
    }


def _row_to_alert(row: dict) -> AlertSchema:
    return AlertSchema(
        id=row.get("id"),
        array_name=row.get("array_name", ""),
        vendor=row.get("vendor", "pure"),
        message_id=row.get("message_id"),
        event=row.get("event"),
        severity=row.get("severity", "unknown"),
        component_type=row.get("component_type"),
        component_name=row.get("component_name"),
        opened=row.get("opened"),
        closed=row.get("closed"),
        expected=row.get("expected"),
        actual=row.get("actual"),
        collected_at=row.get("collected_at"),
        teams_notified=str(row["teams_notified"]) if row.get("teams_notified") else None,
        snow_ticket=row.get("snow_ticket"),
        suppressed=bool(row.get("suppressed", 0)),
        resolved=bool(row.get("resolved", 0)),
    )
