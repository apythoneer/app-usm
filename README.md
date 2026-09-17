# SIP — Storage Intelligence Platform (powered by USM)

A vendor-agnostic **single pane of glass** across the enterprise storage fleet.
SIP collects capacity, performance, volume, host, and alert data from **7 storage
vendors** across on-prem datacenters and three clouds (AWS / Azure / GCP), in near
real time — with an interactive Fleet dashboard, capacity analytics, multi-channel
alert paging, a token-authenticated partner API, and a local AI query engine.

> The platform is branded **SIP** in the UI; the backend/codebase remains **USM**
> (Unified Storage Monitoring). "SIP, powered by USM."

> **Live instance:** `http://usodclpsandadm1.corp.intranet:8080` (dashboard) ·
> `:8000/docs` (API, when `DEBUG=true`)

---

## Table of Contents
1. [Architecture](#architecture)
2. [Supported Vendors & Fleet](#supported-vendors--fleet)
3. [Directory Structure](#directory-structure)
4. [Quick Start](#quick-start)
5. [Configuration](#configuration)
6. [Deployment (GitOps)](#deployment-gitops)
7. [API Reference](#api-reference)
8. [External Partner API](#external-partner-api)
9. [Scheduler Jobs](#scheduler-jobs)
10. [Alerting & Paging](#alerting--paging)
11. [Adding a New Vendor](#adding-a-new-vendor)
12. [Storage AI (Chat)](#storage-ai-chat)
13. [Development](#development)
14. [Documentation](#documentation)
15. [Version History](#version-history)

---

## Architecture

The stack is split into an **API tier** and a **collector tier** so a collector
failure can't take the dashboard down, and the API can run multiple workers.

```
┌──────────────────────────────────────────────────────────────────────┐
│                     Docker (network_mode: host)                        │
│                                                                        │
│  ┌────────────────────┐   ┌────────────────────┐   ┌────────────────┐ │
│  │  usm-frontend      │   │  usm-backend       │   │  usm-collector │ │
│  │  Nginx + React     │──▶│  FastAPI  :8000    │   │  FastAPI :8001 │ │
│  │  :8080             │   │  RUN_SCHEDULER=off │   │  APScheduler + │ │
│  │  /api → :8000      │   │  4 uvicorn workers │   │  collectors    │ │
│  │  /api/v1/scheduler │───────────────────────────▶│  (RUN=on, x1)  │ │
│  │      → :8001       │   └─────────┬──────────┘   └───────┬────────┘ │
│  └────────────────────┘             │                      │          │
│  ┌────────────────────┐   ┌─────────┴──────────┐   ┌───────┴────────┐ │
│  │  usm-autoheal      │   │  usm-keepass :2000 │   │  usm-ollama    │ │
│  │  restarts unhealthy│   │  credential broker │   │  LLM :11434    │ │
│  └────────────────────┘   └────────────────────┘   └────────────────┘ │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  pyodbc (ODBC Driver 17)
                        ┌────────▼─────────┐        ┌──────────────────┐
                        │  SQL Server      │        │  Storage APIs    │
                        │  StorMart.USM    │        │  7 vendors ×     │
                        │  (external)      │        │  on-prem + cloud │
                        └──────────────────┘        └──────────────────┘
```

### Components

| Service | Technology | Port | Role |
|---------|-----------|------|------|
| **usm-frontend** | React 18 + Vite + Tailwind, served by Nginx | 8080 | Dashboard SPA; reverse-proxies `/api/` → 8000 and `/api/v1/scheduler/` → 8001 |
| **usm-backend** | FastAPI (uvicorn, `API_WORKERS` workers) | 8000 | REST API only (`RUN_SCHEDULER=false`); safe to scale workers |
| **usm-collector** | FastAPI + APScheduler | 8001 | Runs the scheduler + all vendor collectors (`RUN_SCHEDULER=true`, exactly one); owns DB migrations |
| **usm-autoheal** | willfarrell/autoheal | — | Restarts any container failing its healthcheck (~80s recovery) |
| **usm-keepass** | Flask + gunicorn | 2000 | Credential broker (KeePass REST) with caching |
| **usm-ollama** | Ollama | 11434 | Local LLM for the Text-to-SQL chat |
| **SQL Server** | `StorMart` DB, `USM` schema | 1433 | Persistent store (external host) |

**Reliability layer:** DB-aware `/health/ready` healthcheck (SELECT 1), autoheal,
per-request DB statement timeouts (`DB_QUERY_TIMEOUT`: API 30s / collector 120s),
pyodbc pre-ping, migrations gated to the single collector so multi-worker API
startups don't race.

---

## Supported Vendors & Fleet

| Vendor | API | Collector coverage |
|--------|-----|--------------------|
| **Pure Storage** | REST v1.19 + v2.17 | Capacity, IOPS, latency, NIC util, SAN/queue latency, over-subscription, volumes, hosts, alerts |
| **NetApp ONTAP** | REST `/api` | Capacity, performance, controller load (CPU), NIC util, volumes, aggregates, EMS alerts |
| **NetApp StorageGRID** | REST v3 | Object capacity, buckets, node health/alarms |
| **HPE** (3PAR / Primera / Alletra) | WSAPI | Capacity, IOPS, CPG, VLUNs, host sets, alerts |
| **Oracle ZFS** | REST :215 | Pool capacity, LUNs + NFS shares, problems |
| **Hitachi VSP** | Config Manager REST | Pool capacity, LDEVs, host groups, alerts |
| **Dell EMC** (Unity / PowerStore) | Unity REST | Capacity, LUNs, hosts, alerts |

**Fleet today:** ~**99 arrays** enabled — On-Prem (ADC/CDC/DDC/IDC/MDC/ODC),
AWS (EUS1/EUS2), Azure (CUS/EUS2), GCP (CUS1/EUS4). Technology split: Block, File
(NAS), Object.

---

## Directory Structure

```
unified_storage_monitoring/
├── backend/
│   ├── Dockerfile                    # Python 3.11 + ODBC Driver 17
│   ├── requirements.txt
│   ├── scripts/gen_external_key.py   # Mint a partner API key (hashed)
│   └── app/
│       ├── main.py                   # FastAPI app + lifespan (scheduler gated by RUN_SCHEDULER)
│       ├── api/
│       │   ├── v1/                   # Internal API (dashboard)
│       │   │   ├── router.py         arrays, volumes, hosts, alerts,
│       │   │   │                     analytics, scheduler, settings, chat
│       │   └── ext/router.py         # External partner API (/api/ext/v1, token auth)
│       ├── collectors/
│       │   ├── base.py               # BaseCollector ABC
│       │   ├── registry.py           # @CollectorRegistry.register()
│       │   ├── scheduler.py          # APScheduler setup + job runner
│       │   ├── alert_utils.py        # Datadog/Teams dispatch, severity overrides
│       │   └── pure/ netapp/ hpe/ oracle/ hitachi/ dell/
│       ├── core/
│       │   ├── config.py             # Pydantic Settings (all env vars)
│       │   ├── external_auth.py      # Partner API key auth (SHA-256, scopes, rate limit)
│       │   ├── security.py           # JWT helpers (scaffolded, not enforced)
│       │   └── exceptions.py
│       ├── db/session.py             # pyodbc pool, cursors, schema init/migrations
│       ├── schemas/                  # Pydantic models
│       └── services/                 # chat, sql_safety, inventory, keepass, notification, stats, capacity_projection
├── frontend/
│   ├── Dockerfile                    # Node build → Nginx
│   ├── nginx.conf                    # SPA + reverse proxy (index.html = no-store)
│   └── src/
│       ├── App.tsx                   # Routes
│       ├── api/                      # Typed axios client
│       ├── pages/                    # Dashboard, Fleet, Volumes, Hosts, Alerts,
│       │                             #   Analytics, Capacity, Settings, Logs, Chat
│       ├── components/               # layout, arrays, charts, chat, common, ui
│       └── styles/globals.css
├── docker/
│   ├── docker-compose.yml            # 6-service stack
│   └── keepass/                      # KeePass sidecar
├── config/arrays.txt                 # Fallback array list (DB is primary)
├── docs/                             # ARCHITECTURE, CAPACITY_ANALYTICS, COLLECTOR_CONTRACT,
│                                     #   DEVELOPMENT, VENDOR_GUIDE, external-api, PROGRESS
├── mcp/                              # MCP servers (dev/docs/ops) for AI tooling
├── scripts/generate_capacity_report.py
├── plans/archive/                    # Historical planning docs (archived)
├── Makefile · .env.example
```

---

## Quick Start

```bash
git clone <repo-url> unified_storage_monitoring
cd unified_storage_monitoring
cp .env.example .env      # set SECRET_KEY, KEEPASS_PASSWORD, etc.

# Build & start the full stack
cd docker
docker compose --env-file ../.env -f docker-compose.yml up -d --build

# Verify
curl http://localhost:8000/ping          # {"status":"ok",...}
curl http://localhost:8000/health/ready   # DB-aware readiness
open  http://localhost:8080               # dashboard
```

---

## Configuration

All config is environment-driven (`.env`, see `.env.example` for the full template).
Selected variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `RUN_SCHEDULER` | `true` | `false` on the API container, `true` on the single collector |
| `API_WORKERS` | `4` | uvicorn workers (API container only) |
| `DB_QUERY_TIMEOUT` | `30` / `120` | Per-request DB statement timeout (API / collector) |
| `SQL_SERVER` / `SQL_DATABASE` / `DB_SCHEMA` | `usidcvsql0252…` / `StorMart` / `USM` | SQL Server target |
| `KEEPASS_URL` / `*_CRED_KEY` | — | Credential broker + per-vendor KeePass entry names |
| `METRICS_INTERVAL` | `300` | Metrics collection interval (s) |
| `VOLUMES_INTERVAL` | `1800` | Volume **+ host** collection interval (s) |
| `ALERTS_INTERVAL` | `300` (prod: `60`) | Alert collection interval (s) |
| `TEAMS_WEBHOOK_URL` / `TEAMS_SEVERITIES` | — | Teams paging (on regardless of Datadog) |
| `DATADOG_ENABLED` / `DATADOG_SEVERITIES` | `false` | Datadog Event-Management paging |
| `DATADOG_NOTIFY_GROUPS` / `DATADOG_NOTIFY_VENDORS` | — | Scope Datadog paging (e.g. Azure Pure only) |
| `EXTERNAL_API_KEYS` | `[]` | Partner API keys (JSON, SHA-256 hashed); empty ⇒ surface disabled |
| `EXTERNAL_API_RATE_LIMIT` | `120` | Per-key requests/min |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `CHAT_ENABLED` | `…:11434` / `qwen2.5:3b` / `true` | Local LLM chat |

Credentials live in KeePass and are served by the **usm-keepass** sidecar; each
array maps to a `cred_key`. **Secrets never live in the repo.**

---

## Deployment (GitOps)

Changes ship through pull requests, not direct edits on the server:

1. Branch → PR (reviewed & merged on GitHub; **no Claude branding** in commits/PRs).
2. On the host `usodclpsandadm1` (`/export/home/ad64490/unified_storage_monitoring`):
   ```bash
   git fetch origin main -q
   git tag -f rollback-pre-<name> HEAD        # rollback point
   git reset --hard origin/main
   cd docker
   docker compose --env-file ../.env -f docker-compose.yml up -d --build <services>
   ```
3. Rebuild rule: **backend change → `usm-backend` + `usm-collector`**; frontend
   change → `usm-frontend`. Schema migrations run in the collector's
   `init_database()`.
4. Verify health + endpoints; for frontend, confirm the served bundle hash changed
   (`index.html` is `no-store`, so a stale view is browser cache — hard-reload).

Makefile shortcuts (`make up`, `make logs`, `make status`, `make restart`) exist for
local use.

---

## API Reference

Base URL: `http://<host>:8000/api/v1` (proxied at `:8080/api/v1`).

- **Arrays** — `GET /arrays`, `/arrays/fleet-stats`, `/arrays/{name}`; managed CRUD under `/arrays/managed…`; `POST /arrays/managed/{name}/verify`.
- **Volumes / Hosts** — `GET /volumes`, `GET /hosts` (paginated; `array_name`, `vendor`, `search`, `limit`, `offset`); host↔volume mapping embedded.
- **Alerts** — `GET /alerts` (filters: severity, resolved, array); severity overrides + Datadog paging controls under `/settings`.
- **Analytics** — `fleet-stats`, `fleet-history`, `fleet-trend` (server-bucketed), `fleet-overview` (Fleet dashboard), `capacity-history`, `capacity-breakdown`, `array-growth/{name}`, `top-growers`, `forecast`, `volume-growth/…`.
- **Scheduler** (via `:8001`) — `GET /scheduler/status`; `POST /scheduler/jobs/{id}/run|pause|resume`; `PUT /scheduler/jobs/{id}/interval`.
- **Chat** — `POST /chat/ask` (Text-to-SQL).
- **Health** — `GET /ping`, `GET /health/ready` (DB SELECT 1).

Interactive docs at `/docs` / `/redoc` (exposed only when `DEBUG=true`).

---

## External Partner API

A deliberately narrow, **token-authenticated, read-only** surface for outside
applications, isolated from the internal API — see [`docs/external-api.md`](docs/external-api.md).

- `GET /api/ext/v1/hosts` (scope `hosts:read`) — hosts + attached `volumes`.
- `GET /api/ext/v1/volumes` (scope `volumes:read`) — volumes + attached `hosts`/`host_groups`.
- Per-client keys stored as **SHA-256 hashes** (plaintext never on disk), sent as
  `Authorization: Bearer` or `X-API-Key`; per-key scopes + rate limit; disabled
  (503) until a key is provisioned. Mint keys with `backend/scripts/gen_external_key.py`.

---

## Scheduler Jobs

Runs in the **collector** container (APScheduler), one job per `(vendor, type)`,
staggered per vendor and capped by a thread pool:

| Job | Interval | Purpose |
|-----|----------|---------|
| `{vendor}_metrics` | 5 min | Capacity, IOPS, latency, controller/NIC metrics |
| `{vendor}_volumes` | 30 min | Volume **and host** snapshots |
| `{vendor}_alerts` | 1 min | Alerts/events (drives paging) |
| daily aggregation | 00:05 UTC | Roll up previous day |
| inventory / credential / capacity-alert jobs | daily / hourly / 12h | Sync, refresh, threshold checks |

---

## Alerting & Paging

Collected alerts fan out to channels, with per-array severity overrides:
- **Teams** — always on (webhook).
- **Datadog** (Event Management v2) — runtime on/off switch; only pages from
  enable-point forward; restart-safe (won't re-page); captures the event id + link;
  scopable by group and vendor (e.g. Azure Pure arrays only).

---

## Adding a New Vendor

Plugin-based collector registry — add `backend/app/collectors/<vendor>/` with
`__init__.py`, `client.py`, `metrics.py`, `volumes.py`, `alerts.py`; each collector
extends `BaseCollector` and registers via `@CollectorRegistry.register("<vendor>", "<type>")`.
Ensure the package is imported so the decorators fire, then enable arrays with
`vendor='<vendor>'`. The scheduler auto-creates jobs. See
[`docs/COLLECTOR_CONTRACT.md`](docs/COLLECTOR_CONTRACT.md) and
[`docs/VENDOR_GUIDE.md`](docs/VENDOR_GUIDE.md).

---

## Storage AI (Chat)

Local **Text-to-SQL**: the LLM turns a natural-language question into a
SELECT-only, USM-schema-validated query, runs it, and formats the answer (with
self-correction on failure). Default model `qwen2.5:3b` runs locally on CPU; a
larger `qwen3:30b` is available via an off-network DGX endpoint (opt-in, since it
sends data externally). Access via the **Storage AI** sidebar link or
`POST /api/v1/chat/ask`.

---

## Development

Prereqs: Python 3.11+, Node 18+, Docker, internal DNS access.

```bash
# Backend
cd backend && python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev     # Vite :5173
npm run build                                 # tsc + vite (must pass before deploy)

# Backend tests
cd backend && pytest
```

---

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, data flow |
| [`docs/COLLECTOR_CONTRACT.md`](docs/COLLECTOR_CONTRACT.md) | Collector interface & return shapes |
| [`docs/VENDOR_GUIDE.md`](docs/VENDOR_GUIDE.md) | Per-vendor API notes |
| [`docs/CAPACITY_ANALYTICS.md`](docs/CAPACITY_ANALYTICS.md) | Capacity model & projections |
| [`docs/external-api.md`](docs/external-api.md) | Partner API reference + operator runbook |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Local dev workflow |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Recent progress + pending tasks |

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| **v3.1** | 2026-09 | Collector/API container split + autoheal + multi-worker API; metric expansion (controller load, NIC util, SAN/queue latency, over-subscription); interactive Analytics (server-bucketed trends); **Fleet Overview** dashboard (slicers + capacity treemap); Datadog paging with runtime switch; **external partner API**; SIP rebrand |
| **v3.0.0** | 2026-05 | 7 vendors, Storage AI chat, FastAPI + React |
| **v2.0.0** | 2026-05 | FastAPI rewrite, React dashboard, 5 vendors |
| **v1.0.0** | 2026-03 | Flask + cron, Pure Storage only |
