# Bugfix Sprint 01 — Immediate Fixes

> **Bugs:** BUG-01, BUG-02, BUG-04  
> **Files to modify:** 3 backend files  
> **Risk:** Low — changes are isolated to NetApp collectors and alert lifecycle

---

## BUG-01: NetApp Arrays Show "degraded" Status

### Root Cause

The NetApp metrics collector never sets `array_status` — it only sets `controller_status`. The API endpoint at `arrays.py:124` falls back: `array_status=row.get("array_status") or row.get("controller_status")`. So the "degraded" comes from the `controller_status` logic.

The `controller_status` check in `netapp/metrics.py:116-120` is flawed:

```python
all_healthy = all(
    n.get("health") is True or n.get("is_all_flash_optimized") is not None
    for n in records
)
```

The `is_all_flash_optimized` fallback is incorrect — it has nothing to do with health. If a node returns `health: False` but has `is_all_flash_optimized`, it still passes.

### Fix — `backend/app/collectors/netapp/metrics.py`

1. **Add cluster-level health check** using `GET /api/cluster` which returns a top-level `health` boolean
2. **Fix node health logic** — remove the `is_all_flash_optimized` fallback, use only `n.get("health") is True`
3. **Set both `array_status` AND `controller_status`** in the collected metrics
4. **Update the `save()` method** to write `array_status` to the DB

**Proposed logic:**

```python
# Cluster-level health
cluster_data = self.client.get("cluster", params={"fields": "metric,health"})
if cluster_data:
    cluster_healthy = cluster_data.get("health") is True
    metrics["array_status"] = "healthy" if cluster_healthy else "degraded"

# Node-level health — controller status
nodes_data = self.client.get("cluster/nodes", params={"fields": "health"})
if nodes_data:
    records = nodes_data.get("records", [])
    if records:
        all_nodes_ok = all(n.get("health") is True for n in records)
        metrics["controller_status"] = "healthy" if all_nodes_ok else "degraded"
```

**Also update `save()` SQL** — add `array_status=?` to both UPDATE and INSERT statements.

---

## BUG-02: NetApp Alerts Collector Takes 60 Seconds

### Root Cause

In `netapp/alerts.py:56-63`, the collector calls `get_all("support/ems/events")` with no time filter. ONTAP stores thousands of historical EMS events. Even though `max_records=200` is set per page, `get_all()` auto-paginates and the sheer volume of data + DB lookups per event slows it down.

### Fix — `backend/app/collectors/netapp/alerts.py`

1. **Add time-window filter** — only fetch EMS events from the last 24 hours using ONTAP API filter syntax
2. **Use single-page fetch** instead of `get_all()` — 200 events per 5-min cycle is more than enough
3. **Add `return_records` param** to limit fields returned

**Proposed change:**

```python
from datetime import datetime, timedelta

def collect(self) -> Dict[str, Any]:
    messages = []

    # Only fetch events from the last 24 hours
    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Use single GET instead of get_all to avoid pagination overhead
    data = self.client.get(
        "support/ems/events",
        params={
            "fields": "message.name,message.severity,time,log_message,node.name,index",
            "max_records": "200",
            "order_by": "index desc",
            "time": f">{since}",
        },
    )

    events = data.get("records", []) if data else []
    # ... rest of processing unchanged
```

This should reduce collection time from ~60s to under 5s by eliminating pagination through the entire EMS history.

---

## BUG-04: 170K Active Alerts (Alert Noise)

### Root Cause

Three compounding issues:

1. **NetApp alerts are never resolved** — `save()` inserts with `resolved=0` but never sets `resolved=1`. Unlike Pure (which has `closed` timestamps), NetApp EMS events are point-in-time and have no "closed" concept.

2. **No alert TTL or cleanup** — old alerts accumulate forever. The `history_cleanup` cron job handles `metrics_history` but not `messages`.

3. **Info-level events are kept** — the severity filter skips `informational` and `debug` but keeps `notice` and `warning` (mapped to `info`), which are high-volume noise.

### Fix — Multi-part approach

#### Part A: Auto-resolve old NetApp alerts — `backend/app/collectors/netapp/alerts.py`

Add logic in `save()` to auto-resolve NetApp alerts older than a configurable threshold:

```python
# After saving new alerts, auto-resolve old ones
cursor.execute(
    f"""UPDATE {SCHEMA}.messages
        SET resolved = 1
        WHERE vendor = 'netapp'
          AND resolved = 0
          AND opened < DATEADD(day, -7, GETDATE())""",
)
```

#### Part B: Add alert cleanup to scheduler — `backend/app/collectors/scheduler.py`

Add a scheduled job to purge resolved alerts older than 30 days:

```python
# In build_scheduler(), add:
scheduler.add_job(
    alert_cleanup_job,
    trigger=CronTrigger(hour=2, minute=0),
    id="alert_cleanup",
    name="Purge old resolved alerts",
    replace_existing=True,
)

async def alert_cleanup_job():
    """Delete resolved alerts older than 30 days."""
    from app.db.session import get_db_cursor
    with get_db_cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {SCHEMA}.messages "
            f"WHERE resolved = 1 AND opened < DATEADD(day, -30, GETDATE())"
        )
        deleted = cursor.rowcount
    logger.info(f"Alert cleanup: purged {deleted} resolved alerts older than 30 days")
```

#### Part C: Tighten NetApp severity filtering — `backend/app/collectors/netapp/alerts.py`

Add `notice` to the skip list since it maps to `info` and is very noisy:

```python
_SKIP_SEVERITIES = {"informational", "debug", "notice"}
```

#### Part D: Add configurable alert TTL — `backend/app/core/config.py`

```python
# Alert lifecycle
alert_resolve_days: int = Field(default=7, alias="ALERT_RESOLVE_DAYS")
alert_purge_days: int = Field(default=30, alias="ALERT_PURGE_DAYS")
```

---

## Files Changed Summary

| File | Changes |
|------|---------|
| `backend/app/collectors/netapp/metrics.py` | Fix health check logic, set `array_status`, update `save()` SQL |
| `backend/app/collectors/netapp/alerts.py` | Add 24h time filter, auto-resolve old alerts, skip `notice` severity |
| `backend/app/collectors/scheduler.py` | Add `alert_cleanup` cron job |
| `backend/app/core/config.py` | Add `alert_resolve_days` and `alert_purge_days` settings |

---

## Verification Steps

After deploying these changes:

1. **BUG-01**: Check `GET /api/v1/arrays` — NetApp arrays should show `array_status: healthy` instead of `degraded`
2. **BUG-02**: Check `GET /api/v1/scheduler/status` — `netapp_alerts` duration should drop from ~60s to under 5s
3. **BUG-04**: Check `GET /api/v1/arrays/fleet-stats` — `active_alerts` count should drop significantly after first cleanup cycle
