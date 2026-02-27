# Unified Storage Monitoring (USM) v2

A vendor-agnostic Storage Intelligence Platform built on FastAPI + React.
Monitors Pure Storage today; NetApp, Commvault, and others are next phases.

## Architecture

```
┌──────────────────────────────────────┐
│           Docker (host network)       │
│                                      │
│  ┌─────────────┐   ┌──────────────┐  │
│  │  usm-backend│   │ usm-frontend │  │
│  │  FastAPI    │   │ nginx + React│  │
│  │  :8000      │◄──│  :8080       │  │
│  │             │   └──────────────┘  │
│  │ APScheduler │                     │
│  │ CollectorRegistry                 │
│  └──────┬──────┘                     │
└─────────┼────────────────────────────┘
          │
     ┌────┴────────────────────┐
     │                         │
┌────▼──────┐        ┌─────────▼───────┐
│ SQL Server │        │  Storage Arrays │
│ StorMart   │        │  Pure / NetApp  │
│ (ODBC)    │        │  (REST APIs)    │
└────────────┘        └─────────────────┘
         │
    ┌────▼──────┐
    │  KeePass  │
    │  REST API │
    └───────────┘
```

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env — set SECRET_KEY, optionally TEAMS_WEBHOOK_URL
```

Add arrays to `backend/config/arrays.txt`:
```
# format: array_fqdn [vendor]
purearray01.ctl.intranet pure
purearray02.ctl.intranet pure
```

### 2. Start

```bash
make up          # build + start
make logs        # follow all logs
make status      # container health
```

Frontend: http://localhost:8080
API docs: http://localhost:8000/docs

### 3. Stop

```bash
make down
```

## Makefile Commands

| Command | Description |
|---------|-------------|
| `make up` | Build and start all services |
| `make start` | Start without rebuilding |
| `make down` | Stop and remove containers |
| `make build` | Rebuild images (no cache) |
| `make logs` | Tail all logs |
| `make logs-backend` | Tail backend only |
| `make logs-frontend` | Tail frontend only |
| `make status` | Show container status |
| `make restart` | Restart all services |
| `make shell-backend` | Shell into backend container |
| `make tail-log` | Tail backend log file |

## Adding a New Vendor

1. Create `backend/app/collectors/<vendor>/` with `__init__.py`, `metrics.py`, etc.
2. Decorate each collector class:

```python
from app.collectors.registry import CollectorRegistry
from app.collectors.base import BaseCollector

@CollectorRegistry.register("netapp", "metrics")
class NetAppMetricsCollector(BaseCollector):
    def authenticate(self) -> bool: ...
    def collect(self): ...
    def save(self, result) -> bool: ...
```

3. Import in `backend/app/collectors/<vendor>/__init__.py`:
```python
from app.collectors.netapp import metrics  # noqa: F401
```

4. Add arrays to `arrays.txt` with the new vendor tag:
```
netappfiler01.ctl.intranet netapp
```

No other code changes needed — the scheduler auto-discovers all registered collectors.

## Directory Structure

```
unified_storage_monitoring/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # FastAPI route handlers
│   │   ├── collectors/      # Vendor collector plugins
│   │   │   ├── registry.py  # CollectorRegistry
│   │   │   ├── scheduler.py # APScheduler jobs
│   │   │   └── pure/        # Pure Storage collectors
│   │   ├── core/config.py   # Settings (Pydantic)
│   │   ├── db/session.py    # SQL Server + init_database()
│   │   ├── schemas/         # Vendor-agnostic Pydantic models
│   │   └── services/        # keepass, notification, stats
│   ├── config/arrays.txt    # Array list
│   └── Dockerfile
├── frontend/
│   ├── src/                 # React + TypeScript + Tailwind
│   ├── nginx.conf           # SPA + /api proxy
│   └── Dockerfile
├── docker/
│   └── docker-compose.v2.yml
├── .env.example             # Copy to .env before first run
├── Makefile
└── README.md
```

## Configuration Reference

All non-secret config is inlined in `docker/docker-compose.v2.yml`.
Secrets go in `.env`:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing secret (generate with `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `TEAMS_WEBHOOK_URL` | Teams incoming webhook (optional — leave blank to disable) |
| `SNOW_GROUP` | ServiceNow assignment group (optional) |

Key environment variables (set in docker-compose):

| Variable | Default | Description |
|----------|---------|-------------|
| `SQL_SERVER` | `usidcvsql0252.ctl.intranet` | SQL Server hostname |
| `SQL_DATABASE` | `StorMart` | Database name |
| `SQL_SCHEMA` | `dbo` | Schema for USM tables |
| `KEEPASS_URL` | `http://usodclpsandadm1.corp.intranet:2000/keepass` | KeePass REST API |
| `SQL_CRED_KEY` | `SQLServerDB` | KeePass key for SQL credentials |
| `METRICS_INTERVAL` | `300` | Seconds between metrics polls |
| `VOLUMES_INTERVAL` | `3600` | Seconds between volume inventory polls |
| `ALERTS_INTERVAL` | `120` | Seconds between alert polls |

## Ports

| Service | Port | Notes |
|---------|------|-------|
| FastAPI backend | 8000 | REST API + OpenAPI docs |
| React frontend | 8080 | SPA dashboard |

v1 (Flask) runs on 5050/5051 — both versions can coexist.
