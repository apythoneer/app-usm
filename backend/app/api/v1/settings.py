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
    capacity_alerts_enabled: bool
    capacity_alert_thresholds: str
    capacity_alert_resend_days: int
    capacity_projected_full_days: int
    capacity_alert_trend_days: int
    capacity_alert_check_interval_hours: int



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
        capacity_alerts_enabled=settings.capacity_alerts_enabled,
        capacity_alert_thresholds=settings.capacity_alert_thresholds,
        capacity_alert_resend_days=settings.capacity_alert_resend_days,
        capacity_projected_full_days=settings.capacity_projected_full_days,
        capacity_alert_trend_days=settings.capacity_alert_trend_days,
        capacity_alert_check_interval_hours=settings.capacity_alert_check_interval_hours,
    )


@router.post("/capacity-alerts/run")
async def trigger_capacity_alert_check():
    """Manually trigger the capacity alert check (thresholds + fleet projection)."""
    from app.services.capacity_alerts import run_capacity_alert_checks
    result = await run_in_threadpool(run_capacity_alert_checks)
    return result



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
        "USM v3 — Test Notification",
        "This is a test message from Unified Storage Monitoring v3.",
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
