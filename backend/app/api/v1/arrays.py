"""
Arrays API — current metrics for all storage arrays (any vendor).
Includes managed-array CRUD (add/update/delete/verify) for the Settings UI.
"""

import logging
import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.db.session import get_db_cursor, rows_to_dicts, row_to_dict
from app.schemas.array import (
    ArrayMetrics, ArraySummary,
    ManagedArray, ManagedArrayCreate, ManagedArrayUpdate, ArrayVerifyResult,
)
from app.core.config import get_settings

logger = logging.getLogger("usm.api.arrays")
router = APIRouter(prefix="/arrays", tags=["arrays"])
settings = get_settings()
SCHEMA = settings.db_schema


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_group_map() -> dict:
    """Return {array_name: group}. Primary: managed_arrays DB. Fallback: arrays.txt."""
    try:
        with get_db_cursor() as cursor:
            cursor.execute(f"SELECT array_name, group_label FROM {SCHEMA}.managed_arrays")
            rows = rows_to_dicts(cursor, cursor.fetchall())
        if rows:
            return {r["array_name"]: r["group_label"] for r in rows if r.get("group_label")}
    except Exception:
        pass

    # Fallback to arrays.txt
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


def _row_to_managed(row: dict) -> ManagedArray:
    # Map vendor to valid VendorType — use "unknown" for unsupported vendors
    from app.schemas.array import VendorType
    valid_vendors = {"pure", "netapp", "hpe", "oracle", "hitachi", "commvault", "dell", "veeam", "nimble", "unknown"}
    vendor = row.get("vendor", "unknown")
    if vendor not in valid_vendors:
        vendor = "unknown"

    return ManagedArray(
        id=row.get("id"),
        array_name=row.get("array_name", ""),
        vendor=vendor,
        group_label=row.get("group_label"),
        cred_key=row.get("cred_key"),
        enabled=bool(row.get("enabled", 1)),
        array_fqdn=row.get("array_fqdn"),
        array_serial=row.get("array_serial"),
        model=row.get("model"),
        site=row.get("site"),
        technology=row.get("technology"),
        category=row.get("category"),
        usage_label=row.get("usage_label"),
        disposition=row.get("disposition"),
        oem=row.get("oem"),
        support_provider=row.get("support_provider"),
        install_date=str(row["install_date"]) if row.get("install_date") else None,
        eosl_date=str(row["eosl_date"]) if row.get("eosl_date") else None,
        maint_end_date=str(row["maint_end_date"]) if row.get("maint_end_date") else None,
        mgmt_ip=row.get("mgmt_ip"),
        monitoring_status=row.get("monitoring_status"),
        dim_sync_at=str(row["dim_sync_at"]) if row.get("dim_sync_at") else None,
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


# ── Managed arrays CRUD (must be before /{array_name} to avoid path conflict) ─

@router.get("/managed", response_model=List[ManagedArray])
async def list_managed_arrays():
    """List all arrays in the managed_arrays table."""
    def _fetch():
        with get_db_cursor() as cursor:
            cursor.execute(f"SELECT * FROM {SCHEMA}.managed_arrays ORDER BY array_name")
            return rows_to_dicts(cursor, cursor.fetchall())
    rows = await run_in_threadpool(_fetch)
    return [_row_to_managed(r) for r in rows]


@router.post("/managed", response_model=ManagedArray, status_code=201)
async def add_managed_array(body: ManagedArrayCreate):
    """Add a new array to managed_arrays."""
    def _insert():
        with get_db_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {SCHEMA}.managed_arrays (array_name, vendor, group_label, cred_key) VALUES (?, ?, ?, ?)",
                (body.array_name, body.vendor, body.group_label, body.cred_key),
            )
            cursor.execute(
                f"SELECT * FROM {SCHEMA}.managed_arrays WHERE array_name = ?",
                (body.array_name,),
            )
            return row_to_dict(cursor, cursor.fetchone())
    try:
        row = await run_in_threadpool(_insert)
        _invalidate_scheduler_cache()
        return _row_to_managed(row)
    except Exception as e:
        if "UNIQUE" in str(e).upper() or "UK_" in str(e):
            raise HTTPException(status_code=409, detail=f"Array '{body.array_name}' already exists")
        raise


@router.put("/managed/{array_name}", response_model=ManagedArray)
async def update_managed_array(array_name: str, body: ManagedArrayUpdate):
    """Update group_label and/or enabled for a managed array."""
    def _update():
        sets = []
        params: list = []
        if body.vendor is not None:
            sets.append("vendor = ?")
            params.append(body.vendor)
        if body.group_label is not None:
            sets.append("group_label = ?")
            params.append(body.group_label)
        if body.cred_key is not None:
            sets.append("cred_key = ?")
            params.append(body.cred_key)
        if body.enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if body.enabled else 0)
        if body.array_fqdn is not None:
            sets.append("array_fqdn = ?")
            params.append(body.array_fqdn)
        if body.mgmt_ip is not None:
            sets.append("mgmt_ip = ?")
            params.append(body.mgmt_ip)
        if body.monitoring_status is not None:
            sets.append("monitoring_status = ?")
            params.append(body.monitoring_status)
        if not sets:
            raise HTTPException(status_code=400, detail="Nothing to update")
        sets.append("updated_at = GETDATE()")
        params.append(array_name)
        with get_db_cursor() as cursor:
            cursor.execute(
                f"UPDATE {SCHEMA}.managed_arrays SET {', '.join(sets)} WHERE array_name = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Array not found")
            cursor.execute(
                f"SELECT * FROM {SCHEMA}.managed_arrays WHERE array_name = ?", (array_name,)
            )
            return row_to_dict(cursor, cursor.fetchone())
    row = await run_in_threadpool(_update)
    _invalidate_scheduler_cache()
    return _row_to_managed(row)


@router.delete("/managed/{array_name}")
async def delete_managed_array(array_name: str):
    """Remove an array from managed_arrays."""
    def _delete():
        with get_db_cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {SCHEMA}.managed_arrays WHERE array_name = ?", (array_name,)
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Array not found")
    await run_in_threadpool(_delete)
    _invalidate_scheduler_cache()
    return {"success": True, "deleted": array_name}


@router.post("/managed/{array_name}/verify", response_model=ArrayVerifyResult)
async def verify_array(array_name: str):
    """Test KeePass credentials and live API connectivity for an array."""
    def _verify():
        from app.services.keepass import get_credentials
        result = ArrayVerifyResult(array_name=array_name)

        # Look up vendor + cred_key from managed_arrays
        vendor = "pure"
        cred_key = None
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    f"SELECT vendor, cred_key FROM {SCHEMA}.managed_arrays WHERE array_name=?",
                    (array_name,),
                )
                row = cursor.fetchone()
                if row:
                    vendor = row[0] or "pure"
                    cred_key = row[1]
        except Exception:
            pass

        # Derive cred_key if not explicitly set
        if not cred_key:
            if vendor == "pure":
                cred_key = f"PureStorage_API_{array_name}"
            else:
                result.error = f"No cred_key configured for {vendor} array — set KeePass Key in Settings"
                return result

        # Step 1: KeePass lookup
        try:
            creds = get_credentials(cred_key)
            result.keepass_ok = bool(creds.get("password") or creds.get("username"))
        except Exception as e:
            result.error = f"KeePass ({cred_key}): {e}"
            return result

        # Step 2: Live API connectivity — vendor-specific
        if result.keepass_ok:
            try:
                if vendor == "netapp":
                    from app.collectors.netapp.client import NetAppClient
                    client = NetAppClient(array_name, cred_key)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("cluster")
                        if info:
                            ver = info.get("version", {})
                            result.version = ver.get("full", ver.get("generation", ""))
                        client.disconnect()
                    else:
                        result.error = "Authentication failed — check username/password"
                else:
                    from app.collectors.pure.client import PureClient
                    client = PureClient(array_name)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("array")
                        if info:
                            arr = info[0] if isinstance(info, list) else info
                            result.version = arr.get("version")
                        client.disconnect()
                    else:
                        result.error = "Authentication failed — check API token"
            except Exception as e:
                result.error = f"API: {e}"

        return result
    return await run_in_threadpool(_verify)


def _invalidate_scheduler_cache():
    """Tell the scheduler to reload arrays on the next job run."""
    try:
        from app.collectors.scheduler import invalidate_arrays_cache
        invalidate_arrays_cache()
    except Exception:
        pass


# ── Fleet stats ───────────────────────────────────────────────────────────────

@router.get("/fleet-stats")
async def get_fleet_stats():
    """Aggregate metrics across all arrays for the fleet stats dashboard row."""
    return await run_in_threadpool(_fetch_fleet_stats)


# ── Array list + detail ───────────────────────────────────────────────────────

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
