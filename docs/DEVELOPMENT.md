# USM v3 — Development & Deployment Guide

> **Purpose:** Everything needed to develop USM locally and deploy to the host. Served by the `usm-docs` MCP server.

---

## 1. Prerequisites

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose
- Access to internal DNS (`*.ctl.intranet`, `*.corp.intranet`)
- SSH access to `usodclpsandadm1.corp.intranet` (deploy target)

---

## 2. Local Development

### Backend (without Docker)
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend (without Docker)
```bash
cd frontend
npm install
npm run dev      # Vite dev server on :5173 (proxies API to :8000)
```

### Full stack (Docker)
```bash
make up          # build + start all 4 containers
make logs        # follow logs
make status      # container status
make down        # stop
```

---

## 3. Deployment Workflow (Build Local → Push → Deploy on Host)

Since the host is **not** a remote dev server, the workflow is:

```bash
# 1. Develop & test locally
make up
curl http://localhost:8000/ping

# 2. Commit & push to GitHub
git add -A
git commit -m "feat: ..."
git push origin main

# 3. SSH to host and pull
ssh sanadmin@usodclpsandadm1.corp.intranet
cd /opt/sanadmin/unified_storage_monitoring   # adjust to actual path
git pull origin main

# 4. Rebuild & restart on host
docker compose -f docker/docker-compose.yml up -d --build

# 5. Verify on host
curl http://localhost:8000/health
curl http://localhost:8080
```

> **Alternative (SCP):** If git is not available on the host, build locally then `scp` the changed files over, then rebuild. Keep a consistent target directory.

---

## 4. Debugging Collectors

```bash
# Tail a specific vendor's collector log
docker exec usm-backend tail -f /app/logs/collectors/pure_metrics.log

# Check which collectors are registered
curl http://localhost:8000/health | jq .collectors

# Check scheduler job status (last run, duration, success counts)
curl http://localhost:8000/api/v1/scheduler/status | jq

# Manually trigger a job
curl -X POST http://localhost:8000/api/v1/scheduler/jobs/pure_metrics/run

# Pause / resume a job
curl -X POST http://localhost:8000/api/v1/scheduler/jobs/pure_metrics/pause
curl -X POST http://localhost:8000/api/v1/scheduler/jobs/pure_metrics/resume
```

Per-collector logs live in `/app/logs/collectors/{vendor}_{type}.log` (mounted to `../logs` on host).

---

## 5. Database Access

- Primary: SQL Server `usidcvsql0252.ctl.intranet`, DB `StorMart`, schema `USM`.
- Read cache: SQLite at `/app/data/usm_cache.db` (mounted to `../data`).
- Schema auto-migrates on startup (`init_database()` is idempotent).

```bash
# Inspect SQLite cache from inside container
docker exec usm-backend sqlite3 /app/data/usm_cache.db "SELECT COUNT(*) FROM metrics_current;"
```

---

## 6. Testing

```bash
cd backend
pytest                    # run all tests
pytest tests/test_collectors.py -v
```

> Backend tests live in `backend/tests/`. Aim to test collector `collect()` parsing logic with mocked API responses, and API endpoints with a test client.

---

## 7. MCP Servers (Developer Tooling)

Three MCP servers live in `mcp/` to accelerate development:

| Server | Purpose | Run |
|--------|---------|-----|
| `usm-docs` | Serves architecture/schema/vendor docs to AI agents | `python mcp/usm_docs_server.py` |
| `usm-dev` | Scaffolds collectors, validates schema/env | `python mcp/usm_dev_server.py` |
| `usm-ops` | Live fleet status, scheduler, alerts, Teams test | `python mcp/usm_ops_server.py` |

Setup:
```bash
cd mcp
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

The `usm-ops` server reads `USM_API_BASE` env var (default `http://usodclpsandadm1.corp.intranet:8000`) to connect to the running instance. See `mcp/README.md`.

---

## 8. Common Tasks

| Task | How |
|------|-----|
| Add a vendor | See [VENDOR_GUIDE.md](VENDOR_GUIDE.md) or use `usm-dev` MCP `scaffold_vendor` |
| Add an API endpoint | New module in `backend/app/api/v1/`, include in `router.py` |
| Add a scheduled job | Add to `build_scheduler()` in `collectors/scheduler.py` |
| Change collector intervals | Set `METRICS_INTERVAL` / `VOLUMES_INTERVAL` / `ALERTS_INTERVAL` env vars |
| Configure Teams alerts | Set `TEAMS_WEBHOOK_URL` in `.env` or via Settings UI |
| Configure ServiceNow | Set `SNOW_*` env vars — see [.env.example](../.env.example) |

---

## 9. Project Layout Quick Reference

```
backend/app/
  main.py              # FastAPI entry + lifespan
  core/config.py       # all env settings (Pydantic)
  db/session.py        # SQL Server + schema init
  db/cache.py          # SQLite read cache
  api/v1/              # REST endpoints
  collectors/          # vendor plugins (base, registry, scheduler + 7 vendors)
  services/            # chat, keepass, notification, servicenow, inventory, stats
  schemas/             # Pydantic models
frontend/src/
  pages/               # Dashboard, Volumes, Hosts, Alerts, Analytics, Settings
  components/          # layout, chat
  api/                 # typed axios clients
docs/                  # this documentation
mcp/                   # MCP servers
plans/                 # design docs & trackers
```
