"""
Settings API — read/write runtime configuration.
Settings are persisted to the app_settings DB table so they survive restarts.
"""

import os
import logging
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional, List

from app.core.config import get_settings
from app.collectors.registry import CollectorRegistry
from app.db.session import get_db_cursor, rows_to_dicts

logger = logging.getLogger("usm.settings")
router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()
SCHEMA = settings.db_schema


class SettingsResponse(BaseModel):
    app_name: str
    version: str
    db_server: str
    db_database: str
    db_schema: str
    metrics_interval: int
    volumes_interval: int
    alerts_interval: int
    teams_webhook_configured: bool
    teams_webhook_url: str
    registered_vendors: list
    registered_collectors: dict


class NotificationUpdate(BaseModel):
    teams_webhook_url: str


# ── DB helpers for app_settings table ─────────────────────────────────────────

def _db_get_setting(key: str) -> Optional[str]:
    """Read a single setting from app_settings table."""
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                f"SELECT setting_value FROM {SCHEMA}.app_settings WHERE setting_key=?",
                (key,),
            )
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.warning(f"Could not read setting '{key}': {e}")
        return None


def _db_set_setting(key: str, value: str) -> None:
    """Upsert a setting in app_settings table."""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"MERGE {SCHEMA}.app_settings AS target "
            f"USING (SELECT ? AS setting_key) AS source "
            f"ON target.setting_key = source.setting_key "
            f"WHEN MATCHED THEN UPDATE SET setting_value=?, updated_at=GETDATE() "
            f"WHEN NOT MATCHED THEN INSERT (setting_key, setting_value) VALUES (?, ?);",
            (key, value, key, value),
        )


def load_persisted_settings() -> None:
    """Load persisted settings from DB into the in-memory Settings object.
    Called once at startup after init_database().
    """
    url = _db_get_setting("teams_webhook_url")
    if url:
        settings.teams_webhook_url = url
        logger.info("Loaded teams_webhook_url from database")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SettingsResponse)
async def get_app_settings():
    """Return current application settings."""
    url = settings.teams_webhook_url or ""
    return SettingsResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        db_server=settings.sql_server,
        db_database=settings.sql_database,
        db_schema=settings.db_schema,
        metrics_interval=settings.metrics_interval,
        volumes_interval=settings.volumes_interval,
        alerts_interval=settings.alerts_interval,
        teams_webhook_configured=bool(url),
        teams_webhook_url=url,
        registered_vendors=CollectorRegistry.vendors(),
        registered_collectors=CollectorRegistry.summary(),
    )


@router.put("/notifications")
async def update_notifications(body: NotificationUpdate):
    """Update Teams webhook URL — persisted to database."""
    settings.teams_webhook_url = body.teams_webhook_url
    await run_in_threadpool(_db_set_setting, "teams_webhook_url", body.teams_webhook_url)
    return {"success": True, "teams_webhook_configured": bool(settings.teams_webhook_url)}


@router.post("/notifications/test")
async def test_notifications():
    """Send a test Teams message to verify the webhook."""
    from app.services.notification import send_teams_message
    ok = await run_in_threadpool(
        send_teams_message,
        "USM v2 — Test Notification",
        "This is a test message from Unified Storage Monitoring v2.",
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Teams notification failed — check webhook URL")
    return {"success": True}


@router.get("/database")
async def get_database_info():
    """Row counts and last-updated timestamps for all USM tables."""
    schema = settings.db_schema

    def _fetch():
        tables = [
            ("messages",              "collected_at"),
            ("metrics_current",       "collected_at"),
            ("metrics_history",       "collected_at"),
            ("daily_stats",           "collected_at"),
            ("volumes_cache",         "last_updated"),
            ("hosts_cache",           "last_updated"),
            ("host_groups_cache",     "last_updated"),
            ("protection_groups_cache", "last_updated"),
            ("managed_arrays",        "updated_at"),
            ("app_settings",          "updated_at"),
        ]
        results = []
        with get_db_cursor() as cursor:
            for table, ts_col in tables:
                try:
                    cursor.execute(
                        f"SELECT COUNT(*), MAX(CAST({ts_col} AS NVARCHAR(50))) "
                        f"FROM {schema}.{table}"
                    )
                    row = cursor.fetchone()
                    results.append({
                        "table_name": table,
                        "row_count": row[0] if row else 0,
                        "last_updated": row[1] if row else None,
                    })
                except Exception as e:
                    results.append({
                        "table_name": table,
                        "row_count": None,
                        "last_updated": None,
                        "error": str(e),
                    })
        return results

    return await run_in_threadpool(_fetch)


@router.get("/logs")
async def get_logs(lines: int = 500):
    """Return the last N lines of the backend log file."""
    if lines < 1 or lines > 5000:
        raise HTTPException(status_code=400, detail="lines must be 1–5000")

    log_path = os.path.join(settings.log_dir, "usm_backend.log")

    def _tail(path: str, n: int) -> List[str]:
        if not os.path.exists(path):
            return []
        # Memory-efficient tail: read last 512KB
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 512 * 1024)
            f.seek(-read_size, 2)
            raw = f.read().decode("utf-8", errors="replace")
        all_lines = raw.splitlines()
        return all_lines[-n:]

    log_lines = await run_in_threadpool(_tail, log_path, lines)
    return {"lines": len(log_lines), "data": log_lines}


# ── KeePass browser ───────────────────────────────────────────────────────

@router.get("/keepass-entries")
async def list_keepass_entries():
    """Browse KeePass groups and entry names (no passwords exposed)."""
    from app.services.keepass import browse_entries
    entries = await run_in_threadpool(browse_entries)
    if not entries:
        return {"groups": {}, "total_entries": 0}
    total = sum(len(v) for v in entries.values())
    return {"groups": entries, "total_entries": total}


# ── Inventory sync ─────────────────────────────────────────────────────────

@router.post("/inventory-sync")
async def trigger_inventory_sync():
    """Manually trigger array inventory sync from DimStorageFinance."""
    from app.services.inventory import sync_from_dim_storage_finance
    stats = await run_in_threadpool(sync_from_dim_storage_finance)
    # Invalidate arrays cache so collectors see new arrays
    from app.collectors.scheduler import invalidate_arrays_cache
    invalidate_arrays_cache()
    return stats


@router.get("/inventory-summary")
async def inventory_summary():
    """Summary of managed arrays by vendor, site, and monitoring status."""
    def _query():
        with get_db_cursor() as cur:
            cur.execute(f"""
                SELECT vendor, COUNT(*) as total,
                    SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) as enabled,
                    SUM(CASE WHEN monitoring_status = 'active' THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN monitoring_status = 'no_collector' THEN 1 ELSE 0 END) as no_collector,
                    SUM(CASE WHEN monitoring_status = 'decomming' THEN 1 ELSE 0 END) as decomming
                FROM {SCHEMA}.managed_arrays
                GROUP BY vendor ORDER BY total DESC
            """)
            vendor_rows = rows_to_dicts(cur, cur.fetchall())

            cur.execute(f"""
                SELECT site, COUNT(*) as total
                FROM {SCHEMA}.managed_arrays
                WHERE site IS NOT NULL
                GROUP BY site ORDER BY total DESC
            """)
            site_rows = rows_to_dicts(cur, cur.fetchall())

            cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.managed_arrays")
            total = cur.fetchone()[0]

            cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.managed_arrays WHERE dim_sync_at IS NOT NULL")
            synced = cur.fetchone()[0]

        return {
            "total_arrays": total,
            "synced_from_dim": synced,
            "by_vendor": vendor_rows,
            "by_site": site_rows,
        }

    return await run_in_threadpool(_query)


@router.get("/debug-oracle-api")
async def debug_oracle_api():
    """Temporary: probe Oracle ZFS API response structure from inside the container."""
    def _probe():
        import requests, urllib3
        urllib3.disable_warnings()
        from app.services.keepass import get_credentials

        # Pick first enabled oracle array
        with get_db_cursor() as cur:
            cur.execute(
                f"SELECT TOP 1 array_name, array_fqdn, mgmt_ip, cred_key "
                f"FROM {SCHEMA}.managed_arrays "
                f"WHERE vendor='oracle' AND enabled=1"
            )
            row = cur.fetchone()
            if not row:
                return {"error": "No enabled oracle arrays"}
            cols = [c[0] for c in cur.description]
            arr = dict(zip(cols, row))

        fqdn = arr.get("array_fqdn") or arr.get("mgmt_ip") or arr["array_name"]
        cred_key = arr.get("cred_key", "ZFS_root")
        creds = get_credentials(cred_key)
        base = f"https://{fqdn}:215/api"
        auth = (creds["username"], creds["password"])
        result = {"array": arr["array_name"], "host": fqdn}

        # Pools list
        try:
            r = requests.get(f"{base}/storage/v1/pools", auth=auth, verify=False, timeout=15)
            pools = r.json().get("pools", [])
            result["pools_count"] = len(pools)
            if pools:
                p0 = pools[0]
                result["pool_item_keys"] = sorted(p0.keys())
                result["pool_format"] = "nested" if "pool" in p0 and isinstance(p0.get("pool"), dict) else "flat"
                pool_name = p0.get("name") or (p0.get("pool", {}) or {}).get("name", "")
                result["first_pool_name"] = pool_name
                pool_status = p0.get("status") or (p0.get("pool", {}) or {}).get("status", "")
                result["first_pool_status"] = pool_status

                # Find online pool
                online_pool = None
                for p in pools:
                    pn = p.get("name") or (p.get("pool", {}) or {}).get("name", "")
                    ps = p.get("status") or (p.get("pool", {}) or {}).get("status", "")
                    if ps == "online":
                        online_pool = pn
                        break

                if online_pool:
                    # Projects
                    r2 = requests.get(f"{base}/storage/v1/pools/{online_pool}/projects",
                                      auth=auth, verify=False, timeout=15)
                    projs = r2.json().get("projects", [])
                    result["projects_count"] = len(projs)
                    if projs:
                        pr0 = projs[0]
                        result["project_item_keys"] = sorted(pr0.keys())
                        result["project_format"] = "nested" if "project" in pr0 and isinstance(pr0.get("project"), dict) else "flat"
                        pn = pr0.get("name") or (pr0.get("project", {}) or {}).get("name", "")
                        result["first_project_name"] = pn

                        if pn:
                            # LUNs
                            r3 = requests.get(f"{base}/storage/v1/pools/{online_pool}/projects/{pn}/luns",
                                              auth=auth, verify=False, timeout=15)
                            luns = r3.json().get("luns", [])
                            result["luns_count"] = len(luns)
                            if luns:
                                result["lun_item_keys"] = sorted(luns[0].keys())
                                result["lun_format"] = "nested" if "lun" in luns[0] and isinstance(luns[0].get("lun"), dict) else "flat"

                            # Filesystems
                            r4 = requests.get(f"{base}/storage/v1/pools/{online_pool}/projects/{pn}/filesystems",
                                              auth=auth, verify=False, timeout=15)
                            fss = r4.json().get("filesystems", [])
                            result["filesystems_count"] = len(fss)
                            if fss:
                                result["fs_item_keys"] = sorted(fss[0].keys())
                                result["fs_format"] = "nested" if "filesystem" in fss[0] and isinstance(fss[0].get("filesystem"), dict) else "flat"
        except Exception as e:
            result["error"] = str(e)

        return result

    return await run_in_threadpool(_probe)
