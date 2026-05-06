"""
Hosts API — host inventory across all arrays and vendors.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from app.db.session import get_db_cursor, rows_to_dicts
from app.schemas.host import HostSchema, HostGroupSchema
from app.core.config import get_settings

router = APIRouter(prefix="/hosts", tags=["hosts"])
settings = get_settings()
SCHEMA = settings.db_schema


def _fetch_hosts(
    array_name: Optional[str],
    search: Optional[str],
    vendor: Optional[str],
    limit: int,
    offset: int,
) -> dict:
    where, params = [], []
    if array_name:
        where.append("array_name = ?")
        params.append(array_name)
    if search:
        where.append("(host_name LIKE ? OR array_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if vendor:
        where.append("vendor = ?")
        params.append(vendor)

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    with get_db_cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.hosts_cache{where_clause}", params)
        total = cursor.fetchone()[0]

    sql = f"SELECT * FROM {SCHEMA}.hosts_cache{where_clause} ORDER BY array_name, host_name OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    with get_db_cursor() as cursor:
        cursor.execute(sql, params + [offset, limit])
        rows = rows_to_dicts(cursor, cursor.fetchall())

    return {"total": total, "rows": rows}


def _fetch_hgroups(array_name: Optional[str]) -> List[dict]:
    with get_db_cursor() as cursor:
        if array_name:
            cursor.execute(
                f"SELECT * FROM {SCHEMA}.host_groups_cache WHERE array_name=? ORDER BY hgroup_name",
                (array_name,),
            )
        else:
            cursor.execute(f"SELECT * FROM {SCHEMA}.host_groups_cache ORDER BY array_name, hgroup_name")
        return rows_to_dicts(cursor, cursor.fetchall())


@router.get("")
async def list_hosts(
    array_name: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    vendor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=5000),
    offset: int = Query(default=0, ge=0),
):
    result = await run_in_threadpool(_fetch_hosts, array_name, search, vendor, limit, offset)
    return {
        "total": result["total"],
        "limit": limit,
        "offset": offset,
        "data": [_row_to_host(r) for r in result["rows"]],
    }


@router.get("/groups", response_model=List[HostGroupSchema])
async def list_host_groups(array_name: Optional[str] = Query(default=None)):
    rows = await run_in_threadpool(_fetch_hgroups, array_name)
    return [_row_to_hgroup(r) for r in rows]


def _safe_json(val) -> list:
    if not val:
        return []
    try:
        return json.loads(val) if isinstance(val, str) else val
    except Exception:
        return []


def _row_to_host(row: dict) -> dict:
    return HostSchema(
        array_name=row.get("array_name", ""),
        vendor=row.get("vendor", "pure"),
        host_name=row.get("host_name", ""),
        iqn=row.get("iqn"),
        wwn=row.get("wwn"),
        nqn=row.get("nqn"),
        host_group=row.get("host_group"),
        volumes=_safe_json(row.get("volumes")),
        last_updated=str(row["last_updated"]) if row.get("last_updated") else None,
    ).model_dump()


def _row_to_hgroup(row: dict) -> HostGroupSchema:
    return HostGroupSchema(
        array_name=row.get("array_name", ""),
        vendor=row.get("vendor", "pure"),
        hgroup_name=row.get("hgroup_name", ""),
        hosts=_safe_json(row.get("hosts")),
        volumes=_safe_json(row.get("volumes")),
        last_updated=str(row["last_updated"]) if row.get("last_updated") else None,
    )
