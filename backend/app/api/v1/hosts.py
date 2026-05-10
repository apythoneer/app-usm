"""
Hosts API — host inventory across all arrays and vendors.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

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


class HostStorageRequest(BaseModel):
    """Request body for host storage report."""
    servers: List[str]


@router.post("/storage-report")
async def host_storage_report(body: HostStorageRequest):
    """
    For a list of server names, return volume count + aggregate provisioned/used capacity.
    Joins hosts_cache (host→volume mapping) with volumes_cache (volume size/used).
    Handles case-insensitive, partial matching on host_name.
    """
    def _report():
        results = []
        with get_db_cursor() as cursor:
            for server in body.servers:
                # Find all hosts matching this server name (case-insensitive, partial match)
                cursor.execute(
                    f"SELECT array_name, host_name, volumes FROM {SCHEMA}.hosts_cache "
                    f"WHERE UPPER(host_name) LIKE UPPER(?)",
                    (f"%{server}%",),
                )
                host_rows = rows_to_dicts(cursor, cursor.fetchall())

                if not host_rows:
                    results.append({
                        "server_name": server,
                        "found": False,
                        "arrays": [],
                        "volume_count": 0,
                        "total_provisioned_bytes": 0,
                        "total_used_bytes": 0,
                        "total_provisioned_tb": 0,
                        "total_used_tb": 0,
                    })
                    continue

                total_prov = 0
                total_used = 0
                vol_count = 0
                arrays_seen = set()

                for hr in host_rows:
                    array_name = hr.get("array_name", "")
                    arrays_seen.add(array_name)
                    vol_names = _safe_json(hr.get("volumes"))

                    for vol_name in vol_names:
                        # Look up volume in volumes_cache
                        cursor.execute(
                            f"SELECT size, used FROM {SCHEMA}.volumes_cache "
                            f"WHERE array_name = ? AND volume_name = ?",
                            (array_name, vol_name),
                        )
                        vol_row = cursor.fetchone()
                        if vol_row:
                            size = vol_row[0] or 0
                            used = vol_row[1] or 0
                            total_prov += size
                            total_used += used
                            vol_count += 1

                results.append({
                    "server_name": server,
                    "found": True,
                    "arrays": sorted(arrays_seen),
                    "volume_count": vol_count,
                    "total_provisioned_bytes": total_prov,
                    "total_used_bytes": total_used,
                    "total_provisioned_tb": round(total_prov / (1024**4), 2),
                    "total_used_tb": round(total_used / (1024**4), 2),
                })

        return results

    return await run_in_threadpool(_report)


def _row_to_hgroup(row: dict) -> HostGroupSchema:
    return HostGroupSchema(
        array_name=row.get("array_name", ""),
        vendor=row.get("vendor", "pure"),
        hgroup_name=row.get("hgroup_name", ""),
        hosts=_safe_json(row.get("hosts")),
        volumes=_safe_json(row.get("volumes")),
        last_updated=str(row["last_updated"]) if row.get("last_updated") else None,
    )
