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
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.hosts_cache WITH (NOLOCK){where_clause}", params)
        total = cursor.fetchone()[0]

    sql = f"SELECT * FROM {SCHEMA}.hosts_cache WITH (NOLOCK){where_clause} ORDER BY array_name, host_name OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    with get_db_cursor() as cursor:
        cursor.execute(sql, params + [offset, limit])
        rows = rows_to_dicts(cursor, cursor.fetchall())

    return {"total": total, "rows": rows}


def _fetch_hgroups(array_name: Optional[str]) -> List[dict]:
    with get_db_cursor() as cursor:
        if array_name:
            cursor.execute(
                f"SELECT * FROM {SCHEMA}.host_groups_cache WITH (NOLOCK) WHERE array_name=? ORDER BY hgroup_name",
                (array_name,),
            )
        else:
            cursor.execute(f"SELECT * FROM {SCHEMA}.host_groups_cache WITH (NOLOCK) ORDER BY array_name, hgroup_name")
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
    Uses an optimized SQL approach: bulk-fetch hosts, then single JOIN to volumes.
    """
    def _report():
        results = []
        servers = body.servers

        with get_db_cursor() as cursor:
            # Step 1: Bulk-fetch all matching hosts in one query using OR conditions
            # Build batches of 100 to avoid SQL param limits
            all_host_data = {}  # server_name -> {arrays, vol_keys, array_vendors}
            batch_size = 50

            for i in range(0, len(servers), batch_size):
                batch = servers[i:i + batch_size]
                # Use LIKE prefix match (case-insensitive) to find hosts
                # HPE stores "aa16-04_ossarcp1", Pure uses "azeus2sqlbnrn45", etc.
                for srv in batch:
                    cursor.execute(
                        f"SELECT host_name, array_name, volumes, vendor FROM {SCHEMA}.hosts_cache WITH (NOLOCK) "
                        f"WHERE UPPER(host_name) LIKE UPPER(?) + '%'",
                        (srv,),
                    )
                    rows_found = cursor.fetchall()
                    for row in rows_found:
                        host_name = row[0]
                        array_name = row[1]
                        volumes_json = row[2]
                        vendor = row[3] if len(row) > 3 else ""
                        if srv not in all_host_data:
                            all_host_data[srv] = {"arrays": set(), "vol_keys": [], "array_vendors": {}}
                        all_host_data[srv]["arrays"].add(array_name)
                        all_host_data[srv]["array_vendors"][array_name] = vendor
                        vol_names = _safe_json(volumes_json)
                        for vn in vol_names:
                            all_host_data[srv]["vol_keys"].append((array_name, vn))

            # Step 2: Collect all unique (array_name, volume_name) pairs
            all_vol_keys = set()
            for sd in all_host_data.values():
                all_vol_keys.update(sd["vol_keys"])

            # Step 3: Bulk-fetch volume sizes in batches
            vol_sizes = {}  # (array_name, volume_name) -> (size, used)
            vol_key_list = list(all_vol_keys)

            for i in range(0, len(vol_key_list), batch_size):
                batch = vol_key_list[i:i + batch_size]
                # Build query with OR conditions for each (array, volume) pair
                conditions = " OR ".join(
                    ["(array_name = ? AND volume_name = ?)"] * len(batch)
                )
                params = []
                for a, v in batch:
                    params.extend([a, v])

                if conditions:
                    cursor.execute(
                        f"SELECT array_name, volume_name, size, used "
                        f"FROM {SCHEMA}.volumes_cache WITH (NOLOCK) WHERE {conditions}",
                        params,
                    )
                    for row in cursor.fetchall():
                        vol_sizes[(row[0], row[1])] = (row[2] or 0, row[3] or 0)

            # Step 4: Aggregate per server with per-array breakdown
            for server in servers:
                if server not in all_host_data:
                    results.append({
                        "server_name": server,
                        "found": False,
                        "arrays": [],
                        "array_breakdown": [],
                        "volume_count": 0,
                        "total_provisioned_bytes": 0,
                        "total_used_bytes": 0,
                        "total_provisioned_tb": 0,
                        "total_used_tb": 0,
                    })
                    continue

                sd = all_host_data[server]
                total_prov = 0
                total_used = 0
                vol_count = 0

                # Per-array aggregation
                array_stats: Dict[str, Dict] = {}
                for key in sd["vol_keys"]:
                    arr_name = key[0]
                    if arr_name not in array_stats:
                        array_stats[arr_name] = {"volumes": 0, "provisioned": 0, "used": 0}
                    if key in vol_sizes:
                        size, used = vol_sizes[key]
                        total_prov += size
                        total_used += used
                        vol_count += 1
                        array_stats[arr_name]["volumes"] += 1
                        array_stats[arr_name]["provisioned"] += size
                        array_stats[arr_name]["used"] += used

                # Build per-array breakdown with vendor info
                array_breakdown = []
                for arr_name in sorted(array_stats.keys()):
                    st = array_stats[arr_name]
                    vendor = sd.get("array_vendors", {}).get(arr_name, "")
                    array_breakdown.append({
                        "array_name": arr_name,
                        "vendor": vendor,
                        "volume_count": st["volumes"],
                        "provisioned_bytes": st["provisioned"],
                        "used_bytes": st["used"],
                        "provisioned_tb": round(st["provisioned"] / (1024**4), 2),
                        "used_tb": round(st["used"] / (1024**4), 2),
                    })

                results.append({
                    "server_name": server,
                    "found": True,
                    "arrays": sorted(sd["arrays"]),
                    "array_breakdown": array_breakdown,
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
