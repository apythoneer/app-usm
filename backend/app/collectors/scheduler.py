"""
APScheduler-based collector scheduler.
Reads array list, instantiates registered collectors, runs on configured intervals.
Adding a new vendor = register its collectors + ensure arrays are in config.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Any, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.collectors.registry import CollectorRegistry, load_all_collectors
from app.schemas.array import ArrayConfig
from app.core.config import get_settings

logger = logging.getLogger("usm.scheduler")
settings = get_settings()

# Thread pool for blocking collector I/O
_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="collector")

# In-memory job status store
_job_status: Dict[str, Dict[str, Any]] = {}


def load_arrays() -> List[ArrayConfig]:
    """Load array configs from arrays.txt. Format: `array_name [vendor]`"""
    path = settings.arrays_config_file
    arrays = []
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

    scheduler = AsyncIOScheduler(timezone="UTC")
    registered = CollectorRegistry.all_registered()

    intervals = {
        "metrics": settings.metrics_interval,
        "volumes": settings.volumes_interval,
        "alerts": settings.alerts_interval,
    }

    for vendor, ctype, _ in registered:
        interval = intervals.get(ctype, 300)
        job_id = f"{vendor}_{ctype}"

        scheduler.add_job(
            run_collector_job,
            trigger=IntervalTrigger(seconds=interval),
            id=job_id,
            args=[vendor, ctype],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(f"Scheduled {job_id} every {interval}s")

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
