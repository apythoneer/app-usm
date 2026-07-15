# USM v3 — Architecture Reference

> **Purpose:** Single source of truth for the USM system architecture. This document is also served by the `usm-docs` MCP server so AI agents can answer architecture questions without re-reading the entire codebase.

---

## 1. System Overview

Unified Storage Monitoring (USM) is a vendor-agnostic Storage Intelligence Platform that monitors ~84 enterprise storage arrays across 7 vendors from a single dashboard. It runs as a 4-container Docker Compose stack on `usodclpsandadm1.corp.intranet`.

```
┌───────────────────────────────────────────────────────────┐
│                Docker (host network)                       │
│                                                           │
│  ┌──────────────────┐    ┌─────────────────┐              │
│  │  usm-backend     │    │  usm-frontend   │              │
│  │  FastAPI  :8000  │◄───│  Nginx+React    │              │
│  │  APScheduler     │    │  :8080          │              │
│  │  CollectorRegistry│    └─────────────────┘              │
│  └────────┬─────────┘                                     │
│           │                                               │
│  ┌────────┴──────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  usm-keepass  │  │  usm-ollama  │  │  Storage APIs │  │
│  │  Flask :2000  │  │  LLM :11434  │  │  7 vendors    │  │
│  └───────────────┘  └──────────────┘  └───────────────┘  │
└───────────────────────────────────────────────────────────┘
          │
     ┌────▼────────────┐
     │  SQL Server      │
     │  StorMart.USM    │
     │  (ODBC Driver)   │
     └──────────────────┘
```

---

## 2. Container Topology

All containers use `network_mode: host` to reach internal corporate DNS (`*.ctl.intranet`, `*.corp.intranet`).

| Service | Tech | Port | Purpose |
|---------|------|------|---------|
| `usm-backend` | FastAPI + APScheduler | 8000 | REST API, collector scheduler, Text-to-SQL pipeline |
| `usm-frontend` | React + Vite + Nginx | 8080 | Dashboard SPA |
| `usm-keepass` | Flask + gunicorn (4 workers) | 2000 | Credential REST API with 24h TTL cache |
| `usm-ollama` | Ollama (qwen2.5:3b) | 11434 | Local LLM for chat (12GB mem limit) |
| SQL Server (external) | StorMart.USM | 1433 | Persistent storage |

---

## 3. Data Flow

### Collection Path (write)
```
APScheduler (interval trigger)
  → run_collector_job(vendor, type)         # scheduler.py
    → ThreadPoolExecutor (max 5 workers)
      → Collector.run()                      # base.py
        → authenticate()                     # vendor client
        → collect()                          # vendor metrics/volumes/alerts
        → save()           → SQL Server      # primary write
        → _write_to_cache() → SQLite         # dual-write read cache (non-fatal)
```

### Read Path
```
Frontend (React Query polling)
  → GET /api/v1/...                          # FastAPI endpoint
    → run_in_threadpool(sync_fn)             # keeps event loop non-blocking
      → SQLite cache (sub-ms) OR SQL Server  # depends on endpoint
```

### Chat Path (Text-to-SQL)
```
POST /api/v1/chat/ask
  → ChatService.ask()                        # services/chat.py
    → _generate_sql()  → Ollama LLM
    → validate_sql()   → sql_safety.py       # SELECT-only, USM schema, blocked patterns
    → _execute_sql()   → SQL Server
    → _fix_sql() (retry on error) → Ollama
    → _format_answer() → Ollama              # natural-language summary
```

---

## 4. Threading & Concurrency Model

- **FastAPI** runs async; blocking DB/HTTP calls are wrapped in `run_in_threadpool`.
- **APScheduler** uses `AsyncIOScheduler` (timezone UTC).
- **Collectors** run in a `ThreadPoolExecutor(max_workers=5)` to limit concurrent SQL Server connections.
- **Vendor stagger**: each vendor's jobs are offset by 10s to avoid simultaneous DB pressure.
- **pyodbc pooling** is enabled (`pyodbc.pooling = True`); connection string is cached after first KeePass fetch.

---

## 5. Database Layer

### SQL Server (primary — `StorMart.USM`)
Schema auto-initializes on startup via `init_database()` in `db/session.py` (idempotent `IF NOT EXISTS`).

Tables: `messages`, `metrics_current`, `metrics_history`, `daily_stats`, `volumes_cache`, `hosts_cache`, `host_groups_cache`, `protection_groups_cache`, `managed_arrays`, `app_settings`. See [COLLECTOR_CONTRACT.md](COLLECTOR_CONTRACT.md) for full column definitions.

### SQLite (read cache — `/app/data/usm_cache.db`)
- WAL mode, thread-local connections.
- Mirrors hot tables: `metrics_current`, `volumes_cache`, `hosts_cache`, `messages`.
- Collectors dual-write; API can read from cache for sub-ms latency.
- Non-fatal: if SQLite fails, the app falls back to SQL Server.

---

## 6. Credential Management

```
Collector needs creds
  → keepass.py get_credentials(cred_key)
    → in-memory cache hit? return
    → else GET http://usm-keepass:2000/keepass/{key}
      → KeePass Flask sidecar (60s response cache, 24h TTL)
```

- SQL Server creds: `SQL_CRED_KEY` (default `SQLServerDB`)
- Pure default: `PureStorage_API_{array_name}` if no `cred_key` set
- Daily refresh job at 00:00 UTC re-fetches all cached keys.

---

## 7. Startup Sequence (`main.py` lifespan)

1. Configure logging (file + stream).
2. `test_connection()` — verify SQL Server reachable.
3. `init_database()` — create/migrate schema.
4. `load_persisted_settings()` — load runtime config from `app_settings`.
5. `init_cache()` — initialize SQLite cache.
6. `build_scheduler()` — load collectors, prefetch credentials, register jobs.
7. `scheduler.start()`.

---

## 8. Scheduler Jobs (21 total)

| Job | Trigger | Description |
|-----|---------|-------------|
| `{vendor}_metrics` | every `METRICS_INTERVAL` (300s) | Capacity, IOPS, latency |
| `{vendor}_volumes` | every `VOLUMES_INTERVAL` (1800s) | Volume/LUN snapshots |
| `{vendor}_alerts` | every `ALERTS_INTERVAL` (600s) | Alerts/events |
| `daily_stats` | cron 00:05 UTC | Daily fleet aggregation |
| `history_cleanup` | cron 01:00 UTC | Purge old metrics_history |
| `alert_cleanup` | cron 02:00 UTC | Purge resolved alerts > 30 days |
| `credential_refresh` | cron 00:00 UTC | Refresh KeePass cache |
| `inventory_sync` | cron 03:00 UTC | Sync DimStorageFinance → managed_arrays |

---

## 9. Technology Stack

| Layer | Tech | Version |
|-------|------|---------|
| Backend | FastAPI / Python | 0.109+ / 3.11 |
| Scheduler | APScheduler | AsyncIOScheduler |
| DB | SQL Server (pyodbc/ODBC 17) + SQLite | — |
| Frontend | React / TypeScript / Vite | 18 / 5 / 5 |
| Styling | Tailwind CSS | 3.4 |
| Data fetching | TanStack React Query | 5 |
| Charts | Recharts | 2.10 |
| LLM | Ollama (qwen2.5:3b) | local |
| Notifications | MS Teams MessageCard, ServiceNow | — |

---

## 10. Extension Points

- **New vendor**: add a collector package — see [VENDOR_GUIDE.md](VENDOR_GUIDE.md).
- **New API endpoint**: add a router module under `api/v1/` and include it in `router.py`.
- **New scheduled job**: add to `build_scheduler()` in `scheduler.py`.
- **New notification channel**: add a service under `services/` and wire into the alert pipeline.
