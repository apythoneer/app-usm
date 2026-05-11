# Unified Storage Monitoring (USM) v3

A vendor-agnostic **Storage Intelligence Platform** that provides a single pane of glass across all enterprise storage infrastructure. Built on **FastAPI + React**, USM collects capacity, performance, volume, host, and alert data from 7 storage vendors in real time—plus an AI-powered natural-language query engine.

> **Live instance**: `http://usodclpsandadm1.corp.intranet:8080` (dashboard) / `:8000/docs` (API)

---

## Table of Contents

1. [Architecture](#architecture)
2. [Supported Vendors](#supported-vendors)
3. [Directory Structure](#directory-structure)
4. [Quick Start](#quick-start)
5. [Configuration](#configuration)
6. [Docker Compose Deployment](#docker-compose-deployment)
7. [API Reference](#api-reference)
8. [Scheduler Jobs](#scheduler-jobs)
9. [Adding a New Vendor](#adding-a-new-vendor)
10. [Storage AI (Chat)](#storage-ai-chat)
11. [Development](#development)

---

## Architecture

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

### Components

| Service | Technology | Port | Purpose |
|---------|-----------|------|---------|
| **usm-backend** | FastAPI + APScheduler | 8000 | REST API, collector scheduler, data pipeline |
| **usm-frontend** | React + Vite + Tailwind | 8080 | Dashboard SPA served via Nginx |
| **usm-keepass** | Flask + gunicorn | 2000 | Credential REST API with caching |
| **usm-ollama** | Ollama (qwen2.5:3b) | 11434 | Local LLM for Text-to-SQL chat |
| **SQL Server** | StorMart.USM schema | 1433 | Persistent storage (external) |

---

## Supported Vendors

| Vendor | API | Arrays | Collector Features |
|--------|-----|--------|--------------------|
| **Pure Storage** | REST v2.x | FlashArray, FlashBlade | Capacity, IOPS, latency, volumes, hosts, alerts |
| **NetApp ONTAP** | REST /api | FAS, AFF, CVO | Capacity, performance, volumes, aggregates, EMS alerts |
| **NetApp StorageGRID** | REST v3 | Object storage | Capacity, buckets, node health |
| **HPE 3PAR / Primera / Alletra** | WSAPI | 3PAR 20450, Primera 650 | Capacity, IOPS, CPG metrics, VLUNs, host sets, alerts |
| **Oracle ZFS** | REST :215 | ZS7-2, ZS5-4 | Pool capacity, LUNs + NFS shares, problems |
| **Hitachi VSP** | Config Manager REST | VSP F900 | Pool capacity, LDEVs, host groups, alerts |
| **Dell EMC** | Unity REST | Unity, PowerStore | Capacity, LUNs, hosts, alerts |

**Current fleet**: ~84 arrays actively monitored across 353 discovered (from `DimStorageFinance`).

---

## Directory Structure

```
unified_storage_monitoring/
├── backend/
│   ├── Dockerfile                   # Python 3.11 + ODBC Driver 17
│   ├── requirements.txt
│   └── app/
│       ├── main.py                  # FastAPI app + lifespan (scheduler start)
│       ├── api/v1/                  # REST endpoints
│       │   ├── router.py            # Mounts all sub-routers
│       │   ├── arrays.py            # Array metrics + managed CRUD
│       │   ├── volumes.py           # Volume cache queries
│       │   ├── hosts.py             # Host cache queries
│       │   ├── alerts.py            # Alert/message queries
│       │   ├── analytics.py         # Fleet stats, vendor breakdown
│       │   ├── scheduler.py         # Job status, trigger, enable/disable
│       │   ├── settings.py          # KeePass browsing, config
│       │   └── chat.py              # Storage AI text-to-SQL
│       ├── collectors/              # Vendor collector plugins
│       │   ├── base.py              # BaseCollector ABC
│       │   ├── registry.py          # @CollectorRegistry.register() decorator
│       │   ├── scheduler.py         # APScheduler setup + job runner
│       │   ├── pure/                # Pure Storage collectors
│       │   ├── netapp/              # NetApp ONTAP + StorageGRID
│       │   ├── hpe/                 # HPE 3PAR/Primera/Alletra
│       │   ├── oracle/              # Oracle ZFS
│       │   ├── hitachi/             # Hitachi VSP
│       │   └── dell/                # Dell EMC Unity/PowerStore
│       ├── core/
│       │   ├── config.py            # Pydantic Settings (env vars)
│       │   ├── security.py          # JWT helpers (future)
│       │   └── exceptions.py        # Custom exception classes
│       ├── db/
│       │   └── session.py           # pyodbc connection + cursor context manager
│       ├── schemas/                 # Pydantic request/response models
│       │   ├── array.py, volume.py, host.py, alert.py, auth.py, common.py
│       └── services/
│           ├── chat.py              # Ollama Text-to-SQL pipeline
│           ├── sql_safety.py        # SQL injection prevention
│           ├── inventory.py         # DimStorageFinance sync
│           ├── keepass.py           # KeePass REST client
│           ├── notification.py      # Teams webhook
│           └── stats.py             # Fleet statistics
├── frontend/
│   ├── Dockerfile                   # Node build → Nginx
│   ├── nginx.conf                   # Reverse proxy to backend :8000
│   ├── src/
│   │   ├── App.tsx                  # React Router setup
│   │   ├── api/                     # Typed API client (axios)
│   │   ├── pages/                   # Dashboard, Volumes, Hosts, Alerts, Analytics, Settings, Chat
│   │   ├── components/              # Layout, Sidebar, Charts, ChatPanel
│   │   └── styles/globals.css       # Tailwind CSS
│   └── package.json
├── docker/
│   ├── docker-compose.yml           # 4-service stack (backend, frontend, keepass, ollama)
│   └── keepass/                     # KeePass sidecar (Flask + gunicorn)
├── config/
│   └── arrays.txt                   # Fallback array list (DB is primary)
├── plans/                           # Agile stories, sprint plans, design docs
├── Makefile                         # Convenience commands (make up, make logs, etc.)
└── .env.example                     # Environment variable template
```

---

## Quick Start

### 1. Clone & Configure

```bash
git clone <repo-url> unified_storage_monitoring
cd unified_storage_monitoring
cp .env.example .env
# Edit .env — set SECRET_KEY, KEEPASS_PASSWORD, optionally TEAMS_WEBHOOK_URL
```

### 2. Deploy with Docker Compose

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### 3. Verify

```bash
# Backend health
curl http://localhost:8000/ping
# → {"status":"ok","version":"3.0.0"}

# API docs
open http://localhost:8000/docs

# Dashboard
open http://localhost:8080
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SQL_SERVER` | `usidcvsql0252.ctl.intranet` | SQL Server hostname |
| `SQL_DATABASE` | `StorMart` | Database name |
| `DB_SCHEMA` | `USM` | Schema for all USM tables |
| `SQL_DRIVER` | `ODBC Driver 17 for SQL Server` | ODBC driver |
| `KEEPASS_URL` | `http://...corp.intranet:2000/keepass` | KeePass REST API |
| `SQL_CRED_KEY` | `SQLServerDB` | KeePass entry for SQL creds |
| `PURE_CRED_KEY` | `PureStorage` | KeePass entry for Pure creds |
| `SECRET_KEY` | (change me) | JWT signing key |
| `CORS_ORIGINS` | `["http://localhost:8080"]` | Allowed CORS origins |
| `METRICS_INTERVAL` | `60` | Metrics collection interval (seconds) |
| `VOLUMES_INTERVAL` | `900` | Volume collection interval (seconds) |
| `ALERTS_INTERVAL` | `300` | Alert collection interval (seconds) |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama LLM endpoint |
| `OLLAMA_MODEL` | `qwen2.5:3b` | LLM model for chat |
| `CHAT_ENABLED` | `true` | Enable/disable chat feature |
| `TEAMS_WEBHOOK_URL` | (empty) | Microsoft Teams webhook for notifications |

### Credential Management

Credentials are stored in a KeePass database and served via the **usm-keepass** sidecar container. Each vendor's collector fetches credentials by a configurable `cred_key` mapped per array in `managed_arrays`.

### Database (SQL Server)

All data is stored in `StorMart.USM` schema with these key tables:
- `managed_arrays` — Array inventory with vendor, credentials, enabled flag
- `array_metrics` — Time-series capacity and performance data
- `volumes_cache` — Latest volume snapshots per array
- `hosts_cache` — Latest host/initiator snapshots per array
- `messages` — Alerts and events from all vendors

---

## Docker Compose Deployment

```bash
# Build and start all services
docker compose -f docker/docker-compose.yml up -d --build

# View logs
docker compose -f docker/docker-compose.yml logs -f

# Rebuild backend only
docker compose -f docker/docker-compose.yml build usm-backend
docker compose -f docker/docker-compose.yml up -d usm-backend

# Stop everything
docker compose -f docker/docker-compose.yml down
```

Or use the Makefile shortcuts:
```bash
make up        # Build and start
make logs      # Follow all logs
make restart   # Restart all services
make status    # Show container status
```

---

## API Reference

Base URL: `http://<host>:8000/api/v1`

### Arrays
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/arrays/` | All arrays with latest metrics |
| `GET` | `/arrays/summary` | Fleet summary (counts, capacity) |
| `GET` | `/arrays/managed` | List managed arrays (inventory) |
| `POST` | `/arrays/managed` | Add a managed array |
| `PUT` | `/arrays/managed/{name}` | Update array (enable/disable, cred_key, etc.) |
| `DELETE` | `/arrays/managed/{name}` | Remove a managed array |
| `POST` | `/arrays/managed/{name}/verify` | Test connectivity to an array |

### Volumes
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/volumes/` | All cached volumes (paginated) |
| `GET` | `/volumes/?array_name=X` | Volumes for a specific array |

### Hosts
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/hosts/` | All cached hosts (paginated) |
| `GET` | `/hosts/?array_name=X` | Hosts for a specific array |

### Alerts
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/alerts/` | Active alerts across all arrays |
| `GET` | `/alerts/?severity=critical` | Filter by severity |

### Analytics
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/analytics/fleet-stats` | Aggregate fleet statistics |
| `GET` | `/analytics/vendor-breakdown` | Capacity/count by vendor |

### Scheduler
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/scheduler/jobs` | List all scheduler jobs with status |
| `POST` | `/scheduler/jobs/{id}/trigger` | Manually trigger a job |
| `POST` | `/scheduler/jobs/{id}/pause` | Pause a job |
| `POST` | `/scheduler/jobs/{id}/resume` | Resume a job |

### Chat (Storage AI)
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/chat/ask` | Ask a natural-language question about storage |

### Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/ping` | Health check (returns version) |

Full interactive docs at `/docs` (Swagger UI) or `/redoc`.

---

## Scheduler Jobs

The backend runs **APScheduler** with these recurring jobs:

| Job | Interval | Description |
|-----|----------|-------------|
| `{vendor}_metrics` | 60s | Collect capacity, IOPS, latency per array |
| `{vendor}_volumes` | 15min | Snapshot all volumes/LUNs per array |
| `{vendor}_alerts` | 5min | Collect alerts/events per array |
| `inventory_sync` | Daily 21:00 | Sync `DimStorageFinance` → `managed_arrays` |
| `credential_refresh` | Daily 00:00 | Refresh KeePass credential cache |
| `auto_resolve_alerts` | Hourly | Auto-resolve alerts older than 7 days |
| `stale_array_check` | 10min | Mark arrays with no recent data as stale |

Jobs run in a **ThreadPoolExecutor** (max 10 workers) to avoid saturating SQL Server connections. Each vendor's collectors run independently—a failure in one vendor doesn't affect others.

---

## Adding a New Vendor

USM uses a **plugin-based collector registry**. Adding a new vendor requires 4 files:

### 1. Create the vendor directory

```
backend/app/collectors/newvendor/
├── __init__.py     # Import all collector modules
├── client.py       # REST API client (auth, session, helpers)
├── metrics.py      # Capacity & performance collector
├── volumes.py      # Volume/LUN collector
└── alerts.py       # Alert/event collector
```

### 2. Implement collectors

Each collector extends `BaseCollector` and registers with the decorator:

```python
# backend/app/collectors/newvendor/metrics.py
from app.collectors.base import BaseCollector
from app.collectors.registry import CollectorRegistry

@CollectorRegistry.register("newvendor", "metrics")
class NewVendorMetricsCollector(BaseCollector):
    def collect(self, array_config) -> dict:
        # Fetch data from vendor API
        # Write to SQL Server
        # Return summary dict
        ...
```

### 3. Register the import

Add to [`backend/app/collectors/__init__.py`](backend/app/collectors/__init__.py):
```python
from app.collectors import newvendor  # noqa: F401
```

### 4. Add arrays to inventory

Either add rows to `USM.managed_arrays` via the API/UI, or include them in `DimStorageFinance` for auto-discovery.

The scheduler will automatically detect the new collectors and create jobs for any enabled arrays with `vendor='newvendor'`.

---

## Storage AI (Chat)

USM includes a **Text-to-SQL** chat feature powered by a local LLM (Ollama):

1. User asks a natural-language question (e.g., "Which array has the highest IOPS?")
2. The LLM generates a SQL query against the USM schema
3. SQL safety validation ensures SELECT-only, USM schema, no dangerous patterns
4. Query executes against SQL Server
5. Results are formatted into a human-readable answer
6. If the query fails, the LLM self-corrects and retries

**Privacy**: All processing happens locally—no data leaves the host. The Ollama container runs `qwen2.5:3b` (3B parameter model, ~2GB RAM).

Access via the **"Storage AI"** link in the sidebar, or programmatically:

```bash
curl -X POST http://localhost:8000/api/v1/chat/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How many arrays do we have by vendor?"}'
```

---

## Development

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose
- Access to internal DNS (`*.ctl.intranet`, `*.corp.intranet`)

### Local Backend (without Docker)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate  # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Local Frontend (without Docker)

```bash
cd frontend
npm install
npm run dev  # Vite dev server on :5173
```

### Running Tests

```bash
cd backend
pytest
```

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| **v3.0.0** | 2026-05-11 | 7 vendors, 84 arrays, Storage AI chat, full cleanup |
| **v2.0.0** | 2026-05-05 | FastAPI rewrite, React dashboard, 5 vendors |
| **v1.0.0** | 2026-03-01 | Flask + cron, Pure Storage only |
