"""
APScheduler-based collector scheduler.
Reads array list, instantiates registered collectors, runs on configured intervals.
Adding a new vendor = register its collectors + ensure arrays are in config.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, Any, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.collectors.registry import CollectorRegistry, load_all_collectors
from app.schemas.array import ArrayConfig
from app.core.config import get_settings

logger = logging.getLogger("usm.scheduler")
settings = get_settings()

# Thread pool for blocking collector I/O — limit to 10 concurrent to avoid
# saturating SQL Server connections (each collector uses 1-2 DB connections).
_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="collector")

# In-memory job status store
_job_status: Dict[str, Dict[str, Any]] = {}

# In-memory arrays cache (avoids querying DB on every job run)
_arrays_cache: List[ArrayConfig] = []
_arrays_cache_ts: float = 0
_ARRAYS_CACHE_TTL: int = 60  # seconds


def invalidate_arrays_cache():
    """Force reload on next call to load_arrays()."""
    global _arrays_cache_ts
    _arrays_cache_ts = 0


def load_arrays() -> List[ArrayConfig]:
    """Load array configs. Primary: managed_arrays DB table. Fallback: arrays.txt."""
    global _arrays_cache, _arrays_cache_ts

    if _arrays_cache and (time.monotonic() - _arrays_cache_ts) < _ARRAYS_CACHE_TTL:
        return _arrays_cache

    # Try DB first
    try:
        from app.db.session import get_db_cursor, rows_to_dicts
        with get_db_cursor() as cursor:
            cursor.execute(
                f"SELECT array_name, vendor, group_label, enabled, cred_key, "
                f"model, site, array_fqdn, mgmt_ip "
                f"FROM {settings.db_schema}.managed_arrays"
            )
            rows = rows_to_dicts(cursor, cursor.fetchall())
        if rows:
            arrays = []
            for r in rows:
                try:
                    vendor = r.get("vendor", "unknown")
                    model = r.get("model", "")
                    # Remap NetApp StorageGrid to separate 'storagegrid' vendor
                    # so it uses the StorageGrid collector, not ONTAP
                    if vendor == "netapp" and model and "StorageGrid" in model:
                        vendor = "storagegrid"
                    arrays.append(ArrayConfig(
                        name=r["array_name"],
                        vendor=vendor,
                        group=r.get("group_label"),
                        enabled=bool(r.get("enabled", 1)),
                        cred_key=r.get("cred_key"),
                        model=r.get("model"),
                        site=r.get("site"),
                        array_fqdn=r.get("array_fqdn"),
                        mgmt_ip=r.get("mgmt_ip"),
                    ))
                except Exception as e:
                    logger.debug(f"Skipping array '{r.get('array_name', '?')}': {e}")
            _arrays_cache = arrays
            _arrays_cache_ts = time.monotonic()
            logger.info(f"Loaded {len(arrays)} arrays from managed_arrays table")
            return arrays
    except Exception as e:
        logger.warning(f"Could not load from managed_arrays: {e}")

    # Fallback to arrays.txt
    path = settings.arrays_config_file
    arrays: List[ArrayConfig] = []
    if not os.path.exists(path):
        logger.warning(f"Arrays config not found: {path}")
        return arrays

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            name = parts[0]
            vendor = parts[1] if len(parts) > 1 else "pure"
            group = parts[2] if len(parts) > 2 else None
            arrays.append(ArrayConfig(name=name, vendor=vendor, group=group))  # type: ignore[arg-type]

    _arrays_cache = arrays
    _arrays_cache_ts = time.monotonic()
    logger.info(f"Loaded {len(arrays)} arrays from {path}")
    return arrays


def _run_collector_sync(vendor: str, collector_type: str, array: ArrayConfig) -> Dict:
    """Synchronous collector execution (runs in thread pool)."""
    klass = CollectorRegistry.get(vendor, collector_type)
    if not klass:
        return {"success": False, "error": f"No collector for {vendor}/{collector_type}"}

    try:
        collector = klass(array)
        result = collector.run()
        return result.to_dict()
    except Exception as e:
        logger.error(f"Collector {vendor}/{collector_type}/{array.name} crashed: {e}")
        return {"success": False, "error": str(e), "array_name": array.name}


async def run_collector_job(vendor: str, collector_type: str):
    """Async job wrapper — runs all arrays for a given vendor/type in parallel."""
    job_key = f"{vendor}_{collector_type}"
    arrays = [a for a in load_arrays() if a.vendor == vendor and a.enabled]

    if not arrays:
        logger.debug(f"No {vendor} arrays enabled for {collector_type}")
        return

    logger.info(f"[{job_key}] Starting collection for {len(arrays)} arrays")
    start = datetime.now()

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(_executor, _run_collector_sync, vendor, collector_type, arr)
        for arr in arrays
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
    failed = len(results) - success
    duration = (datetime.now() - start).total_seconds()

    _job_status[job_key] = {
        "last_run": datetime.now().isoformat(),
        "duration_seconds": duration,
        "arrays_total": len(arrays),
        "arrays_success": success,
        "arrays_failed": failed,
        "last_errors": [
            r.get("errors", []) for r in results
            if isinstance(r, dict) and not r.get("success")
        ],
    }

    logger.info(
        f"[{job_key}] Complete — {success}/{len(arrays)} ok in {duration:.1f}s"
    )


def get_job_status() -> Dict[str, Any]:
    return _job_status


def build_scheduler() -> AsyncIOScheduler:
    """
    Build and return the APScheduler instance.
    Registers one job per (vendor, collector_type) combination found in the registry.
    """
    load_all_collectors()
    arrays = load_arrays()

    # Pre-warm the KeePass credential cache sequentially at startup.
    # Only prefetch for enabled arrays whose vendor has a registered collector.
    from app.services.keepass import prefetch_credentials
    registered_vendors = {v for v, _, _ in CollectorRegistry.all_registered()}
    seen_keys = set()
    keys = [settings.sql_cred_key]
    for arr in arrays:
        if not arr.enabled:
            continue
        if arr.vendor not in registered_vendors:
            continue
        cred = arr.cred_key
        if not cred and arr.vendor == "pure":
            cred = f"PureStorage_API_{arr.name}"
        if cred and cred not in seen_keys:
            seen_keys.add(cred)
            keys.append(cred)
    prefetch_credentials(keys)

    scheduler = AsyncIOScheduler(timezone="UTC")
    registered = CollectorRegistry.all_registered()

    intervals = {
        "metrics": settings.metrics_interval,
        "volumes": settings.volumes_interval,
        "alerts": settings.alerts_interval,
    }

    # Stagger vendor schedules to avoid all collectors firing simultaneously.
    # Each vendor gets an offset so they don't all compete for DB connections at once.
    vendor_offset = {}
    vendor_idx = 0
    for vendor, ctype, _ in registered:
        if vendor not in vendor_offset:
            vendor_offset[vendor] = vendor_idx * 10  # 10 second stagger per vendor
            vendor_idx += 1

    for vendor, ctype, _ in registered:
        interval = intervals.get(ctype, 300)
        job_id = f"{vendor}_{ctype}"
        offset = vendor_offset.get(vendor, 0)

        scheduler.add_job(
            run_collector_job,
            trigger=IntervalTrigger(seconds=interval, start_date=datetime.now() + timedelta(seconds=offset)),
            id=job_id,
            args=[vendor, ctype],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(f"Scheduled {job_id} every {interval}s (offset +{offset}s)")

    # Daily aggregation — runs at 00:05 UTC to capture full previous day
    scheduler.add_job(
        _run_daily_stats,
        trigger=CronTrigger(hour=0, minute=5, timezone="UTC"),
        id="daily_stats",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled daily_stats at 00:05 UTC")

    # History cleanup — runs at 01:00 UTC, keeps 7 days by default
    scheduler.add_job(
        _run_history_cleanup,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="history_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled history_cleanup at 01:00 UTC")

    # Alert cleanup — purge resolved alerts older than alert_purge_days (default 30)
    scheduler.add_job(
        _run_alert_cleanup,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="alert_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled alert_cleanup at 02:00 UTC")

    # Credential refresh — midnight UTC, re-fetch all cached KeePass credentials
    scheduler.add_job(
        _run_credential_refresh,
        trigger=CronTrigger(hour=0, minute=0, timezone="UTC"),
        id="credential_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled credential_refresh at 00:00 UTC")

    # Inventory sync — 03:00 UTC, sync arrays from DimStorageFinance
    scheduler.add_job(
        _run_inventory_sync,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="inventory_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Scheduled inventory_sync at 03:00 UTC")

    # Capacity alerting — per-array threshold crossings + fleet projected-full,
    # runs every CAPACITY_ALERT_CHECK_INTERVAL_HOURS (default 12h). First run
    # is delayed 2 minutes after startup so metrics_current/daily_stats have
    # had a chance to populate.
    if settings.capacity_alerts_enabled:
        scheduler.add_job(
            _run_capacity_alerts,
            trigger=IntervalTrigger(
                hours=settings.capacity_alert_check_interval_hours,
                start_date=datetime.now() + timedelta(minutes=2),
            ),
            id="capacity_alerts",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            f"Scheduled capacity_alerts every {settings.capacity_alert_check_interval_hours}h"
        )

    return scheduler




async def _run_daily_stats():
    """Async wrapper for daily stats aggregation."""
    from app.services.stats import calculate_daily_stats
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(_executor, calculate_daily_stats)
    logger.info(f"Daily stats aggregation: {'OK' if ok else 'FAILED'}")


async def _run_history_cleanup():
    """Async wrapper for metrics history cleanup."""
    from app.services.stats import cleanup_old_history
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(_executor, cleanup_old_history)
    logger.info(f"History cleanup: removed {deleted} rows")


def _alert_cleanup_sync() -> int:
    """Delete resolved alerts older than alert_purge_days (default 30)."""
    from app.db.session import get_db_cursor
    purge_days = settings.alert_purge_days
    with get_db_cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {settings.db_schema}.messages "
            f"WHERE resolved = 1 AND TRY_CAST(opened AS DATETIME2) < DATEADD(day, ?, GETDATE())",
            (-purge_days,),
        )
        return cursor.rowcount


async def _run_alert_cleanup():
    """Async wrapper for resolved alert purge."""
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(_executor, _alert_cleanup_sync)
    _job_status["alert_cleanup"] = {
        "last_run": datetime.now().isoformat(),
        "deleted": deleted,
    }
    logger.info(f"Alert cleanup: purged {deleted} resolved alerts older than {settings.alert_purge_days} days")


def _credential_refresh_sync() -> int:
    """Re-fetch all cached KeePass credentials proactively."""
    from app.services.keepass import refresh_all_cached
    return refresh_all_cached()


async def _run_credential_refresh():
    """Midnight credential refresh — re-fetches all cached keys from KeePass."""
    loop = asyncio.get_event_loop()
    refreshed = await loop.run_in_executor(_executor, _credential_refresh_sync)
    _job_status["credential_refresh"] = {
        "last_run": datetime.now().isoformat(),
        "keys_refreshed": refreshed,
    }
    logger.info(f"Credential refresh: {refreshed} keys refreshed from KeePass")


async def _run_inventory_sync():
    """Daily inventory sync — pulls array inventory from DimStorageFinance."""
    from app.services.inventory import sync_from_dim_storage_finance
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(_executor, sync_from_dim_storage_finance)
    _job_status["inventory_sync"] = {
        "last_run": datetime.now().isoformat(),
        **stats,
    }
    # Invalidate arrays cache so collectors pick up new arrays
    invalidate_arrays_cache()
    logger.info(f"Inventory sync: {stats}")


def _capacity_alerts_sync() -> dict:
    from app.services.capacity_alerts import run_capacity_alert_checks
    return run_capacity_alert_checks()


async def _run_capacity_alerts():
    """
    Per-array utilization threshold checks + fleet growth/projected-full check.
    Writes/resolves rows in USM.messages and sends Teams notifications
    (throttled to once per CAPACITY_ALERT_RESEND_DAYS per alert).
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(_executor, _capacity_alerts_sync)
    except Exception as e:
        logger.error(f"Capacity alert check failed: {e}")
        result = {"enabled": True, "error": str(e)}
    _job_status["capacity_alerts"] = {
        "last_run": datetime.now().isoformat(),
        **result,
    }
    logger.info(f"Capacity alerts check: {result}")

