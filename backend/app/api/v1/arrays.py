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
    ArrayMetrics, ArraySummary, ArrayTableRow,
    ManagedArray, ManagedArrayCreate, ManagedArrayUpdate, ArrayVerifyResult,
)
from app.core.config import get_settings

logger = logging.getLogger("usm.api.arrays")
router = APIRouter(prefix="/arrays", tags=["arrays"])
settings = get_settings()
SCHEMA = settings.db_schema

# authenticate() returns only a bool, so a failure can be bad credentials OR an
# unreachable host — don't assert it's the password (it usually wasn't; the host
# info just wasn't passed). The client logs the specific reason.
_CONN_ERR = ("Could not authenticate or connect — check the KeePass credentials and "
             "that the array's FQDN/mgmt_ip is set and reachable from the host.")


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_array_meta() -> dict:
    """Return {array_name: {"group","model","enabled"}} from managed_arrays.

    Supersedes the old group-only map so the dashboard can show the real hardware
    MODEL (not the firmware version) and exclude arrays that are disabled
    (enabled=0) — a disabled/decommissioned array otherwise lingers in
    metrics_current and renders with stale data as if live.
    """
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                f"SELECT array_name, group_label, model, enabled FROM {SCHEMA}.managed_arrays"
            )
            rows = rows_to_dicts(cursor, cursor.fetchall())
        if rows:
            return {
                r["array_name"]: {
                    "group": r.get("group_label"),
                    "model": r.get("model"),
                    # bit column -> bool; default True so an array missing from
                    # managed_arrays is shown rather than hidden.
                    "enabled": bool(r.get("enabled", 1)),
                }
                for r in rows
            }
    except Exception:
        pass

    # Fallback to arrays.txt (group only; everything enabled)
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
                result[parts[0]] = {"group": parts[2], "model": None, "enabled": True}
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
    # SQL Server is the single source of truth for current-state reads.
    #
    # This previously tried the SQLite cache first and returned it whenever
    # total_arrays > 0. That was wrong: the SQLite `messages` table is created
    # but never written (base.py `_write_to_cache` only mirrors metrics and
    # volumes), so the cached active_alerts count was always 0 — and because the
    # cache short-circuited before SQL Server, the dashboard reported 0 active
    # alerts across the whole fleet. It also let dashboard counts (SQLite) drift
    # from the Volumes/Hosts pages (SQL Server), which read a different store.
    #
    # The query below is a single NOLOCK round-trip (see b2d1f59) and the June
    # batching work removed most of the write contention that made it slow, so
    # serving it live is cheap. SQLite remains a write-through mirror and backs
    # /health; it is not a read path until it can mirror alert state correctly.
    with get_db_cursor() as cursor:
        # Exclude arrays disabled in managed_arrays (e.g. decommissioned ones that
        # still have a stale metrics_current row) so fleet totals match the
        # dashboard array list, which now filters the same way.
        NOT_DISABLED = (f"array_name NOT IN "
                        f"(SELECT array_name FROM {SCHEMA}.managed_arrays WITH (NOLOCK) WHERE enabled=0)")
        DIS = f"WHERE {NOT_DISABLED}"
        # Combine all stats into a single query using NOLOCK to avoid blocking
        # during concurrent collector writes. This prevents query timeouts.
        cursor.execute(f"""
            SELECT
                (SELECT COUNT(*) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS total_arrays,
                (SELECT ISNULL(SUM(CAST(capacity_total AS FLOAT)) / 1099511627776.0, 0) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS total_capacity_tb,
                (SELECT ISNULL(SUM(CAST(capacity_used  AS FLOAT)) / 1099511627776.0, 0) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS total_used_tb,
                (SELECT ISNULL(AVG(capacity_used_pct), 0) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS avg_utilization_pct,
                (SELECT ISNULL(SUM(read_iops + write_iops), 0) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS total_iops,
                (SELECT AVG(CAST(read_latency_us AS FLOAT)) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS avg_read_latency_us,
                (SELECT AVG(CAST(write_latency_us AS FLOAT)) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS avg_write_latency_us,
                (SELECT ISNULL(AVG(data_reduction), 1) FROM {SCHEMA}.metrics_current WITH (NOLOCK) {DIS}) AS avg_data_reduction,
                (SELECT COUNT(*) FROM {SCHEMA}.messages WITH (NOLOCK) WHERE resolved=0 AND suppressed=0 AND {NOT_DISABLED}) AS active_alerts,
                (SELECT COUNT(*) FROM {SCHEMA}.volumes_cache WITH (NOLOCK) {DIS}) AS total_volumes,
                (SELECT COUNT(*) FROM {SCHEMA}.hosts_cache WITH (NOLOCK) {DIS}) AS total_hosts
        """)
        row = dict(zip([c[0] for c in cursor.description], cursor.fetchone() or []))

    return row


def _fetch_array_table() -> List[dict]:
    """One NOLOCK round-trip: metrics_current + per-array counts (alerts/volumes/
    hosts) + model from managed_arrays, excluding disabled arrays.

    Reads the same tables (and the same active-alert predicate) as
    _fetch_fleet_stats and the Alerts/Volumes/Hosts pages, so the per-array counts
    sum to the fleet totals — the dashboard and this table can't drift apart.
    """
    with get_db_cursor() as cursor:
        cursor.execute(f"""
            SELECT mc.array_name, mc.vendor, mc.array_status, mc.controller_status,
                   mc.capacity_total, mc.capacity_used, mc.capacity_used_pct,
                   mc.snapshot_space, mc.data_reduction, mc.collected_at,
                   ma.model, ma.group_label,
                   ISNULL(a.cnt, 0) AS active_alerts,
                   ISNULL(v.cnt, 0) AS total_volumes,
                   ISNULL(h.cnt, 0) AS total_hosts
            FROM {SCHEMA}.metrics_current mc WITH (NOLOCK)
            LEFT JOIN {SCHEMA}.managed_arrays ma WITH (NOLOCK)
                   ON ma.array_name = mc.array_name
            LEFT JOIN (SELECT array_name, COUNT(*) cnt FROM {SCHEMA}.messages WITH (NOLOCK)
                       WHERE resolved=0 AND suppressed=0 GROUP BY array_name) a
                   ON a.array_name = mc.array_name
            LEFT JOIN (SELECT array_name, COUNT(*) cnt FROM {SCHEMA}.volumes_cache WITH (NOLOCK)
                       GROUP BY array_name) v
                   ON v.array_name = mc.array_name
            LEFT JOIN (SELECT array_name, COUNT(*) cnt FROM {SCHEMA}.hosts_cache WITH (NOLOCK)
                       GROUP BY array_name) h
                   ON h.array_name = mc.array_name
            WHERE mc.array_name NOT IN
                  (SELECT array_name FROM {SCHEMA}.managed_arrays WITH (NOLOCK) WHERE enabled=0)
            ORDER BY mc.array_name
        """)
        return rows_to_dicts(cursor, cursor.fetchall())


_VALID_VENDORS = {
    "pure", "netapp", "hpe", "oracle", "hitachi", "commvault", "dell",
    "veeam", "nimble", "ibm", "veritas", "storagegrid", "unknown",
}


def _row_to_table(row: dict) -> ArrayTableRow:
    vendor = row.get("vendor") or "unknown"
    if vendor not in _VALID_VENDORS:
        vendor = "unknown"
    return ArrayTableRow(
        array_name=row.get("array_name", ""),
        vendor=vendor,
        model=row.get("model"),
        group=row.get("group_label"),
        # Same fallback as _row_to_summary: some vendors only report controller_status.
        array_status=row.get("array_status") or row.get("controller_status"),
        active_alert_count=row.get("active_alerts") or 0,
        capacity_used_bytes=row.get("capacity_used"),
        capacity_total_bytes=row.get("capacity_total"),
        snapshot_space_bytes=row.get("snapshot_space"),
        capacity_used_pct=row.get("capacity_used_pct"),
        data_reduction=row.get("data_reduction"),
        total_volumes=row.get("total_volumes") or 0,
        total_hosts=row.get("total_hosts") or 0,
        collected_at=row.get("collected_at"),
    )


def _row_to_summary(row: dict, meta: dict) -> ArraySummary:
    name = row.get("array_name", "")
    m = meta.get(name, {})
    return ArraySummary(
        array_name=name,
        vendor=row.get("vendor", "pure"),
        model=m.get("model"),                       # real hardware model
        firmware_version=row.get("purity_version"), # was mislabeled as "model"
        group=m.get("group"),
        capacity_total_bytes=row.get("capacity_total"),
        capacity_used_pct=row.get("capacity_used_pct"),
        data_reduction=row.get("data_reduction"),
        total_iops=(row.get("read_iops") or 0) + (row.get("write_iops") or 0),
        read_latency_us=row.get("read_latency_us"),
        write_latency_us=row.get("write_latency_us"),
        array_status=row.get("array_status") or row.get("controller_status"),
        collected_at=row.get("collected_at"),
    )


def _row_to_metrics(row: dict, meta: dict) -> ArrayMetrics:
    read_iops = row.get("read_iops") or 0
    write_iops = row.get("write_iops") or 0
    name = row.get("array_name", "")
    m = meta.get(name, {})
    return ArrayMetrics(
        array_name=name,
        vendor=row.get("vendor", "pure"),
        model=m.get("model"),                       # was never set -> blank drilldown
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
            "group": m.get("group"),
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

        # Look up vendor + cred_key + host info from managed_arrays. The host info
        # (array_fqdn / mgmt_ip) is REQUIRED: without it every client falls back to
        # the bare array_name as its host, which often does not resolve (all the
        # GCP CVOs, and others), so authenticate() fails to CONNECT and this
        # endpoint used to mislabel that as "Authentication failed".
        vendor = "pure"
        cred_key = fqdn = mgmt_ip = model = None
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    f"SELECT vendor, cred_key, array_fqdn, mgmt_ip, model "
                    f"FROM {SCHEMA}.managed_arrays WHERE array_name=?",
                    (array_name,),
                )
                row = cursor.fetchone()
                if row:
                    vendor = row[0] or "pure"
                    cred_key, fqdn, mgmt_ip, model = row[1], row[2], row[3], row[4]
        except Exception:
            pass

        # NetApp StorageGrid speaks a different API than ONTAP and uses its own
        # client — mirror the collector's remap (scheduler.load_arrays) so verify
        # tests the right endpoint instead of pointing the ONTAP client at it.
        if vendor == "netapp" and model and "storagegrid" in model.lower():
            vendor = "storagegrid"

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
                if vendor == "pure":
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

                elif vendor == "netapp":
                    from app.collectors.netapp.client import NetAppClient
                    client = NetAppClient(array_name, cred_key, fqdn=fqdn, mgmt_ip=mgmt_ip)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("cluster")
                        if info:
                            ver = info.get("version", {})
                            result.version = ver.get("full", ver.get("generation", ""))
                        client.disconnect()
                    else:
                        result.error = _CONN_ERR

                elif vendor == "storagegrid":
                    from app.collectors.netapp.storagegrid_client import StorageGridClient
                    client = StorageGridClient(array_name, cred_key, fqdn=fqdn, mgmt_ip=mgmt_ip)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("grid/config/product-version")
                        if info:
                            pv = info.get("data", info)
                            result.version = pv.get("productVersion") if isinstance(pv, dict) else None
                        client.disconnect()
                    else:
                        result.error = _CONN_ERR

                elif vendor == "hpe":
                    from app.collectors.hpe.client import HPEClient
                    client = HPEClient(array_name, cred_key, fqdn=fqdn, model=model, mgmt_ip=mgmt_ip)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("system")
                        if info and info.get("members"):
                            sys_info = info["members"][0] if isinstance(info["members"], list) else info["members"]
                            result.version = sys_info.get("systemVersion", sys_info.get("softwareVersion", ""))
                        client.disconnect()
                    else:
                        result.error = _CONN_ERR

                elif vendor == "hitachi":
                    from app.collectors.hitachi.client import HitachiVSPClient
                    client = HitachiVSPClient(array_name, cred_key, fqdn=fqdn, mgmt_ip=mgmt_ip)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("configuration/version")
                        if info:
                            result.version = info.get("productName", "") + " " + info.get("controllerVersion", "")
                        client.disconnect()
                    else:
                        result.error = _CONN_ERR

                elif vendor == "dell":
                    from app.collectors.dell.client import DellUnityClient
                    client = DellUnityClient(array_name, cred_key, fqdn=fqdn, mgmt_ip=mgmt_ip)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("types/basicSystemInfo/instances")
                        if info and info.get("entries"):
                            content = info["entries"][0].get("content", {})
                            result.version = content.get("softwareVersion", "")
                        client.disconnect()
                    else:
                        result.error = _CONN_ERR

                elif vendor == "oracle":
                    from app.collectors.oracle.client import OracleZFSClient
                    client = OracleZFSClient(array_name, cred_key, fqdn=fqdn, mgmt_ip=mgmt_ip)
                    if client.authenticate():
                        result.connectivity_ok = True
                        info = client.get("hardware/v1/chassis")
                        if info and info.get("chassis"):
                            ch = info["chassis"][0] if isinstance(info["chassis"], list) else info["chassis"]
                            result.version = ch.get("product", "")
                        client.disconnect()
                    else:
                        result.error = _CONN_ERR

                else:
                    result.error = f"Verify not implemented for vendor '{vendor}'"

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


# Declared before /{array_name} so "table" is not captured as an array name.
@router.get("/table", response_model=List[ArrayTableRow])
async def get_arrays_table():
    """Enriched per-array rows (metrics + alert/volume/host counts) for the
    dashboard Arrays table modal."""
    rows = await run_in_threadpool(_fetch_array_table)
    return [_row_to_table(r) for r in rows]


# ── Array list + detail ───────────────────────────────────────────────────────

@router.get("", response_model=List[ArraySummary])
async def list_arrays(vendor: Optional[str] = Query(default=None)):
    """List all arrays with summary metrics. Filter by ?vendor=pure|netapp|..."""
    rows = await run_in_threadpool(_fetch_arrays, vendor)
    meta = _load_array_meta()
    # Exclude arrays explicitly disabled in managed_arrays. A disabled array can
    # linger in metrics_current with stale data (e.g. ODCSWING, last collected
    # 2 months ago) and would otherwise render on the dashboard as if live.
    return [
        _row_to_summary(r, meta)
        for r in rows
        if meta.get(r.get("array_name", ""), {}).get("enabled", True)
    ]


@router.get("/{array_name}", response_model=ArrayMetrics)
async def get_array(array_name: str):
    """Get full metrics for a single array."""
    row = await run_in_threadpool(_fetch_array, array_name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Array '{array_name}' not found")
    meta = _load_array_meta()
    return _row_to_metrics(row, meta)
