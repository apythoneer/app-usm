# USM v3 — Collector Contract & Database Schema

> **Purpose:** Defines exactly what every vendor collector must implement and the full database schema it writes to. Served by the `usm-docs` MCP server.

---

## 1. The BaseCollector Contract

Every collector subclasses `BaseCollector` (`backend/app/collectors/base.py`) and is registered with the registry decorator.

```python
from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry

@CollectorRegistry.register("vendorname", "metrics")  # or "volumes" / "alerts"
class VendorMetricsCollector(BaseCollector):
    VENDOR = "vendorname"
    COLLECTOR_TYPE = "metrics"

    def authenticate(self) -> bool:
        """Authenticate with the array. Return True on success."""
        ...

    def collect(self) -> dict:
        """Fetch data from the vendor API. Return a data dict."""
        ...

    def save(self, data: dict, result: CollectorResult) -> bool:
        """Persist to SQL Server. Set result.records_saved. Return True on success."""
        ...

    def disconnect(self):       # optional
        """Cleanup — close sessions, etc."""
        ...
```

### Lifecycle (`run()` — implemented in base, do NOT override)
1. `authenticate()` — must return True, else RuntimeError.
2. `collect()` — must return non-empty dict. **"Non-empty" means at least one
   *record*, not merely a non-empty dict.** `{"volumes": {}, "hosts": {}}` is truthy
   and passes base's `if not data` check while carrying no data.
3. `save(data, result)` — must return True.

> ⚠️ **Mandatory for `volumes` collectors:** `save()` MUST return early when the
> collected record sets are empty, e.g.
> ```python
> if not volumes and not hosts:
>     logger.warning(f"[{self.array_name}] No volumes/hosts collected — keeping existing data")
>     return True
> ```
> Vendor clients return `None` on API failure and `collect()` swallows that into empty
> dicts, so an empty result means **"collection failed"**, not "the array has no
> volumes". Without this guard the persistence layer treats every existing row as
> stale and deletes the array's entire inventory on a single transient API error —
> `batch_upsert()` defaults to `delete_missing=True`, and the delete-then-insert
> vendors unconditionally DELETE before INSERT. All six vendors implement this guard;
> new vendors must too.
4. `_write_to_cache(data)` — dual-write to SQLite (automatic, non-fatal).
5. Returns a `CollectorResult` with `success`, `records_saved`, `errors`, `duration_seconds`.

### Required attributes
- `VENDOR` — lowercase vendor key (e.g., `"pure"`, `"netapp"`).
- `COLLECTOR_TYPE` — one of `"metrics"`, `"volumes"`, `"alerts"`.

---

## 2. Expected `collect()` Data Shapes

### metrics collector → dict
```python
{
    "array_name": str,
    "vendor": str,
    "purity_version": str | None,      # or firmware_version
    "read_latency_us": int,
    "write_latency_us": int,
    "read_iops": int,
    "write_iops": int,
    "read_bandwidth": int,             # bytes/sec
    "write_bandwidth": int,
    "capacity_total": int,             # bytes
    "capacity_used": int,              # bytes
    "capacity_used_pct": float,
    "data_reduction": float,
    "total_reduction": float,
    "shared_space": int,
    "snapshot_space": int,
    "volume_space": int,
    "array_status": str,               # "healthy" | "degraded" | "critical"
    "controller_status": str,
    "network_status": str,
    "uptime_seconds": int,
    "uptime_str": str,
    "last_reboot": str,
    "reboot_count": int,
    "collected_at": str,               # ISO timestamp
}
```

### volumes collector → dict
```python
{
    "volumes": {
        "vol_name": {
            "volume_name": str,
            "size": int,               # bytes
            "used": int,               # bytes
            "data_reduction": float,
            "total_reduction": float,
            "snapshots": int,
            "created": str,
            "serial": str,
            "hosts": [str],            # list — stored as JSON
            "host_groups": [str],
            "protection_groups": [str],
        },
        ...
    },
    "hosts": {
        "host_name": {
            "host_name": str,
            "wwn": str,
            "iqn": str,
            "nqn": str,
            "host_group": str,
            "volumes": [str],
        },
        ...
    },
}
```

### alerts collector → dict
```python
{
    "messages": [
        {
            "message_id": int,
            "event": str,
            "severity": str,           # "critical" | "warning" | "info"
            "component_type": str,
            "component_name": str,
            "opened": str,
            "closed": str | None,
            "resolved": bool,
        },
        ...
    ]
}
```

---

## 3. Full Database Schema (SQL Server — `StorMart.USM`)

### managed_arrays — array inventory
| Column | Type | Notes |
|--------|------|-------|
| id | INT IDENTITY PK | |
| array_name | NVARCHAR(255) UNIQUE | |
| vendor | NVARCHAR(50) | default 'pure' |
| group_label | NVARCHAR(100) | aws/azure/gcp/On-Premises |
| enabled | BIT | default 1 |
| cred_key | NVARCHAR(255) | KeePass entry name |
| model, site, technology, category | NVARCHAR | from DimStorageFinance |
| array_fqdn, mgmt_ip, array_serial | NVARCHAR | |
| disposition, oem, support_provider | NVARCHAR | |
| install_date, eosl_date, maint_end_date | DATE | |
| dim_sync_at | DATETIME2 | |
| monitoring_status | NVARCHAR(50) | default 'unknown' |

### metrics_current — latest snapshot per array (UNIQUE array_name)
capacity_total/used (BIGINT bytes), capacity_used_pct (FLOAT), read/write_iops (INT), read/write_latency_us (INT), read/write_bandwidth (BIGINT), data_reduction/total_reduction (FLOAT), array_status/controller_status/network_status (NVARCHAR), uptime_seconds (BIGINT), purity_version (NVARCHAR(100)), collected_at (NVARCHAR — **not** datetime).

### metrics_history — time-series (USE for time-based queries)
collected_at is **DATETIME2** (safe for DATEADD/DATEDIFF). Same metric columns as above.

### messages — alerts/events (UNIQUE array_name + message_id)
message_id (INT), event/severity/component_type/component_name (NVARCHAR), opened/closed (NVARCHAR), teams_notified (DATETIME2), snow_ticket (NVARCHAR(100)), suppressed/resolved (BIT).

### volumes_cache — volume inventory (UNIQUE array_name + volume_name)
size/used (BIGINT bytes), data_reduction/total_reduction (FLOAT), snapshots (INT), serial/created (NVARCHAR), hosts/host_groups/protection_groups (NVARCHAR(MAX) JSON), notes (NVARCHAR(MAX)).

### hosts_cache — host inventory (UNIQUE array_name + host_name)
iqn/wwn/nqn (NVARCHAR(500)), host_group (NVARCHAR), volumes (NVARCHAR(MAX) JSON).

### host_groups_cache, protection_groups_cache
Group inventories with JSON member lists.

### daily_stats — daily aggregation (UNIQUE stat_date)
total_arrays/volumes/hosts (INT), total_capacity_tb/used_tb (FLOAT), avg_utilization_pct (FLOAT), critical/warning/info_alerts (INT).

### app_settings — key/value runtime config (PK setting_key)
setting_key (NVARCHAR(255)), setting_value (NVARCHAR(MAX)), updated_at (DATETIME2).

---

## 4. Save Pattern (SQL Server upsert)

```python
def save(self, data, result):
    from app.db.session import get_db_cursor
    from app.core.config import get_settings
    SCHEMA = get_settings().db_schema
    with get_db_cursor() as cur:
        # MERGE or IF EXISTS UPDATE / ELSE INSERT into metrics_current
        # ... vendor-specific SQL ...
        result.records_saved = N
    return True
```

> **Note:** JSON columns (`hosts`, `volumes`, etc.) store JSON strings but **cannot** be queried with `JSON_VALUE`/`OPENJSON`/`STRING_SPLIT` — this SQL Server does not support them. Treat them as opaque text.

---

## 5. SQLite Cache Dual-Write (automatic)

`BaseCollector._write_to_cache(data)` writes `metrics`, `volumes`, and `hosts` to SQLite automatically after a successful `save()`. You do **not** need to call this manually — just return the correct data shape from `collect()`.

---

## 6. Vendor Reference Table

| Vendor key | API | Client class | Auth |
|------------|-----|--------------|------|
| `pure` | REST v2.x | PureClient | API token |
| `netapp` | ONTAP REST /api | NetAppClient | Basic Auth |
| `storagegrid` | REST v3 | StorageGridClient | Bearer token |
| `hpe` | WSAPI | HPEClient | Session key |
| `oracle` | REST :215 | OracleZFSClient | Basic Auth |
| `hitachi` | Config Manager REST | HitachiClient | Session token |
| `dell` | Unity REST | DellUnityClient | Basic Auth + CSRF |
