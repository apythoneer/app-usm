"""
Settings API — read/write runtime configuration.
"""

import os
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional, List

from app.core.config import get_settings
from app.collectors.registry import CollectorRegistry
from app.db.session import get_db_cursor, rows_to_dicts

router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()


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
    teams_webhook_url: str        # masked
    registered_vendors: list
    registered_collectors: dict


class NotificationUpdate(BaseModel):
    teams_webhook_url: str


@router.get("", response_model=SettingsResponse)
async def get_app_settings():
    """Return current application settings (non-sensitive)."""
    url = settings.teams_webhook_url or ""
    masked = url[:30] + "…" if len(url) > 30 else url
    return SettingsResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        db_server=settings.sql_server,
        db_database=settings.sql_database,
        db_schema=settings.db_schema,
        metrics_interval=settings.metrics_interval,
        volumes_interval=settings.volumes_interval,
        alerts_interval=settings.alerts_interval,
        teams_webhook_configured=bool(settings.teams_webhook_url),
        teams_webhook_url=masked,
        registered_vendors=CollectorRegistry.vendors(),
        registered_collectors=CollectorRegistry.summary(),
    )


@router.put("/notifications")
async def update_notifications(body: NotificationUpdate):
    """Update Teams webhook URL in memory (resets on restart — update .env for persistence)."""
    settings.teams_webhook_url = body.teams_webhook_url
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
