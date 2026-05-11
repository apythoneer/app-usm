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


def _fetch_alerts(
    array_name: Optional[str],
    severity: Optional[str],
    resolved: Optional[bool],
    suppressed: bool,
    limit: int,
) -> List[dict]:
    where = ["suppressed = ?"]
    params: list = [1 if suppressed else 0]

    if array_name:
        where.append("array_name = ?")
        params.append(array_name)
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if resolved is not None:
        where.append("resolved = ?")
        params.append(1 if resolved else 0)

    sql = (
        f"SELECT TOP {limit} * FROM {SCHEMA}.messages WITH (NOLOCK) "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY id DESC"
    )
    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return rows_to_dicts(cursor, cursor.fetchall())


@router.get("", response_model=List[AlertSchema])
async def list_alerts(
    array_name: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    resolved: Optional[bool] = Query(default=None),
    suppressed: bool = Query(default=False),
    limit: int = Query(default=200, le=1000),
):
    """List alerts. Defaults to active (unresolved, unsuppressed) alerts."""
    rows = await run_in_threadpool(
        _fetch_alerts, array_name, severity, resolved, suppressed, limit
    )
    return [_row_to_alert(r) for r in rows]


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
