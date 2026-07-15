# USM v3 — Adding a New Vendor

> **Purpose:** Step-by-step guide for adding a new storage vendor collector. Served by the `usm-docs` MCP server and usable by the `usm-dev` MCP `scaffold_vendor` tool.

---

## Overview

Adding a vendor requires creating a collector package with 4 files and registering it. The scheduler auto-detects registered collectors and creates jobs for any enabled arrays with `vendor='<newvendor>'`.

```
backend/app/collectors/newvendor/
├── __init__.py     # imports all collector modules so @register fires
├── client.py       # REST/API client (auth, session, request helpers)
├── metrics.py      # capacity & performance collector
├── volumes.py      # volume/LUN + host collector
└── alerts.py       # alert/event collector
```

---

## Step 1 — Create the client (`client.py`)

```python
import logging
import requests
import urllib3
from app.services.keepass import get_credentials

urllib3.disable_warnings()
logger = logging.getLogger("usm.newvendor.client")


class NewVendorClient:
    def __init__(self, array_config):
        self.array = array_config
        self.host = array_config.array_fqdn or array_config.mgmt_ip or array_config.name
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = f"https://{self.host}/api"
        self._token = None

    def authenticate(self) -> bool:
        cred_key = self.array.cred_key or f"NewVendor_{self.array.name}"
        creds = get_credentials(cred_key)
        if not creds:
            logger.error(f"No credentials for {cred_key}")
            return False
        try:
            resp = self.session.post(
                f"{self.base_url}/login",
                json={"username": creds["username"], "password": creds["password"]},
                timeout=30,
            )
            resp.raise_for_status()
            self._token = resp.json().get("token")
            self.session.headers["Authorization"] = f"Bearer {self._token}"
            return True
        except Exception as e:
            logger.error(f"Auth failed for {self.host}: {e}")
            return False

    def get(self, path: str, params: dict = None) -> dict:
        resp = self.session.get(f"{self.base_url}/{path}", params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def disconnect(self):
        try:
            self.session.close()
        except Exception:
            pass
```

---

## Step 2 — Metrics collector (`metrics.py`)

```python
from datetime import datetime
from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.newvendor.client import NewVendorClient
from app.db.session import get_db_cursor
from app.core.config import get_settings

SCHEMA = get_settings().db_schema


@CollectorRegistry.register("newvendor", "metrics")
class NewVendorMetricsCollector(BaseCollector):
    VENDOR = "newvendor"
    COLLECTOR_TYPE = "metrics"

    def authenticate(self) -> bool:
        self.client = NewVendorClient(self.array_config)
        return self.client.authenticate()

    def collect(self) -> dict:
        sys = self.client.get("system")
        return {
            "array_name": self.array_name,
            "vendor": self.VENDOR,
            "capacity_total": sys.get("total_bytes", 0),
            "capacity_used": sys.get("used_bytes", 0),
            "capacity_used_pct": sys.get("used_pct", 0.0),
            "read_iops": sys.get("read_iops", 0),
            "write_iops": sys.get("write_iops", 0),
            "read_latency_us": sys.get("read_latency_us", 0),
            "write_latency_us": sys.get("write_latency_us", 0),
            "data_reduction": sys.get("data_reduction", 1.0),
            "array_status": "healthy" if sys.get("healthy") else "degraded",
            "purity_version": sys.get("firmware"),
            "collected_at": datetime.now().isoformat(),
        }

    def save(self, data, result: CollectorResult) -> bool:
        with get_db_cursor() as cur:
            cur.execute(f"""
                MERGE {SCHEMA}.metrics_current AS t
                USING (SELECT ? AS array_name) AS s ON t.array_name = s.array_name
                WHEN MATCHED THEN UPDATE SET
                    vendor=?, capacity_total=?, capacity_used=?, capacity_used_pct=?,
                    read_iops=?, write_iops=?, read_latency_us=?, write_latency_us=?,
                    data_reduction=?, array_status=?, purity_version=?, collected_at=?
                WHEN NOT MATCHED THEN INSERT
                    (array_name, vendor, capacity_total, capacity_used, capacity_used_pct,
                     read_iops, write_iops, read_latency_us, write_latency_us,
                     data_reduction, array_status, purity_version, collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);
            """, (
                data["array_name"],
                data["vendor"], data["capacity_total"], data["capacity_used"], data["capacity_used_pct"],
                data["read_iops"], data["write_iops"], data["read_latency_us"], data["write_latency_us"],
                data["data_reduction"], data["array_status"], data["purity_version"], data["collected_at"],
                data["array_name"], data["vendor"], data["capacity_total"], data["capacity_used"],
                data["capacity_used_pct"], data["read_iops"], data["write_iops"],
                data["read_latency_us"], data["write_latency_us"], data["data_reduction"],
                data["array_status"], data["purity_version"], data["collected_at"],
            ))
            # also append to metrics_history
            cur.execute(f"""
                INSERT INTO {SCHEMA}.metrics_history
                (array_name, vendor, read_iops, write_iops, read_latency_us, write_latency_us,
                 capacity_total, capacity_used, capacity_used_pct, data_reduction)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                data["array_name"], data["vendor"], data["read_iops"], data["write_iops"],
                data["read_latency_us"], data["write_latency_us"], data["capacity_total"],
                data["capacity_used"], data["capacity_used_pct"], data["data_reduction"],
            ))
        result.records_saved = 1
        return True

    def disconnect(self):
        self.client.disconnect()
```

> `volumes.py` and `alerts.py` follow the same structure — see existing vendors (`pure/`, `dell/`) for full examples.

---

## Step 3 — Register imports (`__init__.py`)

```python
# backend/app/collectors/newvendor/__init__.py
from app.collectors.newvendor import metrics   # noqa: F401
from app.collectors.newvendor import volumes   # noqa: F401
from app.collectors.newvendor import alerts    # noqa: F401
```

The top-level `collectors/__init__.py` auto-discovers subpackages via `load_all_collectors()`, so no extra import is usually needed — but verify the package is picked up.

---

## Step 4 — Add arrays to inventory

Either add rows to `USM.managed_arrays` via the Settings UI / API, or include them in `DimStorageFinance` for auto-discovery on the next `inventory_sync`.

```sql
INSERT INTO USM.managed_arrays (array_name, vendor, enabled, cred_key, array_fqdn)
VALUES ('newarray01', 'newvendor', 1, 'NewVendor_newarray01', 'newarray01.corp.intranet');
```

---

## Step 5 — Verify

```bash
# Restart backend, then check the registry picked up the new collectors
curl http://localhost:8000/health | jq .collectors

# Trigger the metrics job manually
curl -X POST http://localhost:8000/api/v1/scheduler/jobs/newvendor_metrics/run

# Confirm data landed
curl "http://localhost:8000/api/v1/arrays?vendor=newvendor"
```

---

## Tips

- Reuse `get_credentials()` from `app.services.keepass` — never hardcode credentials.
- Convert all capacity values to **bytes** and latencies to **microseconds** before saving.
- Set `array_status` to one of `healthy` / `degraded` / `critical`.
- Use `urllib3.disable_warnings()` for self-signed cert arrays.
- Keep `collect()` resilient — wrap optional fields in `.get()` with defaults.
- Use the `usm-dev` MCP `scaffold_vendor` tool to generate these 4 files automatically.
