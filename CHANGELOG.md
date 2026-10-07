# Changelog

All notable changes to **SIP — Storage Intelligence Platform (powered by USM)** are
recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(see [docs/VERSIONING.md](docs/VERSIONING.md)).

> Versions 3.0.0–3.4.0 were **reconstructed from git history + merged PRs** when
> formal versioning was adopted (2026-09-26). From here forward, every release is
> tagged `vX.Y.Z` and cut from the `[Unreleased]` section below.

## [Unreleased]

### Added
- **Prod deployment to AKS (Azure)** (`deploy/overlays/prod`) — hardened overlay
  for the production Rancher cluster `c-jmkcx` / namespace `storage-mgt-tools-na`.
  Every workload is non-root, readonly-rootfs, drops all caps, and disables SA-token
  automount to satisfy the cluster's Azure Policy / Gatekeeper deployment
  safeguards (verified via server-side admission dry-run). Adds an in-namespace
  `keepass-broker` (fed by `keepass-kdbx` + `keepass-master` secrets) that both the
  API and collector use for DB/array creds. Points at the on-prem SQL Server.
- **`deploy-prod` workflow** (`.github/workflows/deploy-prod.yml`) — promotion by
  advancing the `prod` branch to a built `main` SHA; deploys from the `azure-prod`
  self-hosted runner (Azure VM, same VNet as the cluster), and guards that the
  promoted image already exists in ghcr before applying.

### Changed
- **Frontend image is now rootless** (`frontend/Dockerfile` →
  `nginxinc/nginx-unprivileged`) so a single image passes the AKS readonly-rootfs
  policy; drop-in for on-prem compose and dev EKS (both already bind 8080).

### In progress
- **SQL Server → AWS Postgres:** data-layer port (driver + SQL dialect + schema).
  AWS Postgres is directly reachable from AKS prod pods (proven at the protocol
  layer), so no cross-CSP relay is needed — the earlier M365/SharePoint relay plan
  is dropped.
- Stable ingress for the prod/dev dashboards (currently port-forward).

---

## [3.4.0] — 2026-09-23 — Cloud enablement (dev) & CI/CD

First cloud footprint: the stack now builds and deploys to AWS Rancher.

### Added
- **CI pipeline** (`.github/workflows/ci.yml`, #64) — PR checks (backend
  `compileall`, frontend `tsc` + `vite build`) and, on merge to `main`, builds
  `usm-backend` / `usm-frontend` / `usm-keepass` images and pushes them to
  `ghcr.io/apythoneer` (`:<sha>` + `:dev`), with buildx layer cache.
- **Dev deployment to AWS Rancher** (`deploy/` Kustomize, #65) — API, collector,
  and frontend Deployments + Services, a shared ConfigMap, and a k8s nginx config
  that proxies to the in-cluster Services (the baked config assumes host
  networking). Overlay targets `storage-mgt-tools-na-dev`, pointed at the current
  on-prem SQL Server.
- **`deploy-dev` workflow** (#65) — runs on a self-hosted runner inside the corp
  network (the Rancher API is corp-internal), injects a private GHCR pull secret
  from an encrypted Actions secret, applies the overlay, and pins to the built SHA.

### Fixed
- **EKS ADOT auto-instrumentation crash** (#66) — the CloudWatch Application
  Signals webhook injected an OpenTelemetry Python agent whose stale
  `typing_extensions` broke `anyio`/`fastapi` (`ImportError: cannot import name
  'sentinel'`), crash-looping the API + collector. Pods now opt out via
  `instrumentation.opentelemetry.io/inject-*: "false"`.

---

## [3.3.0] — 2026-09-17 — Fleet Overview & repo hardening

### Added
- **Fleet Overview dashboard** (`/fleet`, #59) — cross-filtering slicers
  (cloud / datacenter / vendor / technology), a squarified **capacity treemap**
  (tile = usable capacity, fill = utilization, colour = cloud), used-vs-usable
  breakdown bars, and a sortable fleet table with drill-in. Backed by
  `GET /analytics/fleet-overview` (single NOLOCK join, 60s cache).
- **Smart slicers** (#61) — options that would yield no results are disabled, so
  impossible filter combinations can't be selected.

### Changed
- Repo cleanup + documentation refresh (#63) — removed 17 one-off deploy scripts,
  archived historical plans, rewrote the README for the current architecture,
  refreshed `.env.example`, added `docs/PROGRESS.md`.
- Treemap made responsive with a cleaner "tank-fill" capacity treatment (#60).

### Fixed
- `index.html` served with a single unambiguous `Cache-Control: no-store` (#62) —
  a duplicate `max-age=0` header let some proxies serve a stale bundle.

---

## [3.2.0] — 2026-08-18 — Reliability, metrics & the partner API

### Added
- **Metric expansion** (#55, #56) — NetApp controller load (CPU), NIC utilization
  (Pure + NetApp), Pure SAN/queue latency, over-subscription, port errors and HW
  temperature (via a new Pure v2.17 client path).
- **Interactive Analytics** (#57) — server-side time-bucketed
  `GET /analytics/fleet-trend`, a metric selector, and streamlined multi-select
  filters; fixed disconnected lines + truncated history.
- **External partner API** (#58) — isolated, token-authenticated, read-only
  `/api/ext/v1` (`hosts`, `volumes`) with per-client SHA-256-hashed keys, scopes,
  rate limiting, and a key generator.

### Changed
- Multi-worker API (#54) — the API container runs N uvicorn workers (scheduler
  split out to the collector).
- Per-request DB statement timeouts (#53), tuned per role (API 30s / collector 120s).

### Fixed
- Capacity-history performance (#52) — replaced a `ROW_NUMBER` window over ~1.3M
  rows with a daily `GROUP BY` aggregate + cache, ending a query-timeout / worker
  saturation outage.

---

## [3.1.0] — 2026-08-05 — Alerting, Datadog paging & resilience

### Added
- **Datadog Event-Management paging** (#38, #44–#47) — criticals/warnings paged to
  Datadog, with a runtime on/off switch (#45), captured event id + link (#46), and
  group/vendor scoping (#44, #47). Teams remains on regardless.
- **Custom severity overrides** (#48) — per-array/message severity remapping.
- **Auto-recovery** (#49) — willfarrell/autoheal restarts unhealthy containers;
  DB-aware `/health/ready`.
- **Capacity slicers** (#51) — Power BI-style vendor / site-group / model filters
  on the capacity views.

### Changed
- **Collector / API container split** (#43) — the scheduler + collectors run in a
  separate `usm-collector` container so a collector failure can't take the API down.
- **SIP rebrand** (#50) — UI rebranded to "SIP — Storage Intelligence Platform"
  (backend/codebase stays USM).
- Alert lifecycle unified across all 7 vendors (#33–#41).

### Fixed
- Vendor-specific alert-id overflow/collision handling (Hitachi #37, StorageGrid #39).
- pyodbc pre-ping + resolved-alert filtering (#42).

---

## [3.0.0] — 2026-05-11 — Platform baseline

### Added
- FastAPI + React platform monitoring **7 storage vendors** (Pure, NetApp ONTAP,
  NetApp StorageGRID, HPE, Oracle ZFS, Hitachi VSP, Dell EMC).
- Capacity, performance, volume, host and alert collection on a plugin-based
  collector registry (APScheduler).
- **Storage AI** — local Text-to-SQL chat (Ollama).
- KeePass-backed credential broker; SQL Server (`StorMart.USM`) persistence.

[Unreleased]: https://github.com/apythoneer/app-usm/compare/v3.4.0...HEAD
[3.4.0]: https://github.com/apythoneer/app-usm/releases/tag/v3.4.0
[3.3.0]: https://github.com/apythoneer/app-usm/releases/tag/v3.3.0
[3.2.0]: https://github.com/apythoneer/app-usm/releases/tag/v3.2.0
[3.1.0]: https://github.com/apythoneer/app-usm/releases/tag/v3.1.0
[3.0.0]: https://github.com/apythoneer/app-usm/releases/tag/v3.0.0
