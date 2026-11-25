# Changelog

All notable changes to **ORBITS (formerly SIP) — powered by USM** are
recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(see [docs/VERSIONING.md](docs/VERSIONING.md)).

> Versions 3.0.0–3.4.0 were **reconstructed from git history + merged PRs** when
> formal versioning was adopted (2026-09-26). From here forward, every release is
> tagged `vX.Y.Z` and cut from the `[Unreleased]` section below.

## [Unreleased]

### Added
- **Storage AI: answers stream as they're written** — new `POST /chat/stream`
  (Server-Sent Events). The chat UI shows each step (matching, writing SQL, running
  the query, writing the answer), then the SQL, then the answer word by word. A
  heartbeat every 10 s means slow CPU answers no longer hit the 120 s proxy timeout:
  that only applies to silence now. Falls back to `POST /chat` if streaming is
  unavailable. nginx (`frontend/nginx.conf`, `deploy/base/nginx-configmap.yaml`) has
  an unbuffered location for the stream.
- **Storage AI: similar-question cache (dev)** — Text-to-SQL questions that closely match
  one answered in the last 15 min reuse that answer (0.0 s). Questions that match an
  answer rated 👍 at any age re-run its SQL for fresh data (3 s instead of ~80 s).
  "Closely match" means cosine ≥ 0.93 *and* the same arrays, vendor, period, numbers and
  contrast words, so "over 80%" never reuses "over 90%". Sources rated 👎 are never
  reused. Backed by `USM.chat_log`, so it's shared by every backend process. New
  settings: `CHAT_ANSWER_CACHE` (off by default, on in dev), `CHAT_ANSWER_CACHE_TTL_S`,
  `CHAT_ANSWER_CACHE_SIM`.
- **Storage AI: the suggestion buttons answer instantly** — new question types
  `fleet_capacity`, `capacity_by_vendor`, `peak_iops` (1–90 days, fleet or one array)
  and `uptime` cover the six suggestions on the empty chat screen, which used to take
  80–100 s through Text-to-SQL. Current-value types (busiest, latency, fullest, …) no
  longer answer questions about a past period: "busiest this week" becomes the 7-day
  peak, and the others go to Text-to-SQL. Routing eval: 42/59 right, 0 wrong.
- **Storage AI: 👍/👎 feedback that teaches the assistant** — every answer is logged
  (new additive `USM.chat_log`, with how it was routed) and gets 👍/👎 buttons. After
  a 👎 the user can pick the question type they wanted. That phrasing is saved
  (`USM.chat_intent_examples`), so the next time it's answered directly with no model
  change. A bare 👎 is only recorded. Picking another type, or "none of these", stops
  that phrasing from routing to the wrong type again. New API: `GET /chat/intents`,
  `POST /chat/feedback`, `GET /chat/feedback?only=down|fallthrough` (review misses),
  `GET /chat/examples` and `DELETE /chat/examples/{id}` (retire a learned phrasing).
  Chat responses now include `id` and `route`.
- **Storage AI: instant answers for common questions (dev)** — a new intent router
  (`app/services/chat_intents.py`) answers 16 common question types with vetted
  queries and templated answers instead of free-form Text-to-SQL. Types: fastest
  growing/shrinking arrays, volume growth on an array, fullest / most free, latency,
  IOPS, EOSL, open alerts, unhealthy arrays, hosts or volumes on an array, data
  reduction, array counts, arrays by site, one-array summary. Questions are matched
  to example questions by embedding similarity. Borderline matches go to a tiny
  LLM chooser, and anything else falls through to Text-to-SQL as before. The answer's
  `sql` field shows `-- vetted query · intent=…`. "Which array is growing the most?"
  went from a timeout to a few seconds. New settings `CHAT_INTENT_ROUTER` (off by
  default, on in dev), `CHAT_INTENT_ACCEPT`, `CHAT_INTENT_CONSIDER`,
  `CHAT_INTENT_MARGIN`. Routing eval: `backend/eval/chat_intents.json`.
- **Storage AI: embedding retrieval for the data dictionary (dev)** — the chat picks
  the dictionary entries it adds to the prompt by meaning, not just keywords, using
  `nomic-embed-text` on the sandbox Ollama. New `CHAT_RETRIEVAL`
  (`keyword` | `embedding` | `hybrid`), `CHAT_HYBRID_WEIGHT`, `CHAT_EMBED_TIMEOUT`;
  embedding modes fall back to keyword automatically. On a 34-question eval
  (`backend/eval/`), recall went from 59% (keyword) to 95% (hybrid), and from 21%
  to 89% on paraphrased questions. Enabled on dev only (`CHAT_USE_DICTIONARY=true`,
  `hybrid`); base/prod defaults unchanged. New **Ollama model bundle** workflow
  brings models to the sandbox, which can't reach the Ollama registry.
- **Storage AI on Kubernetes (dev)** — the chat assistant is enabled on dev k8s and
  uses the sandbox LLM (`qwen2.5:3b`) until cloud compute exists. A new authenticated
  proxy (`docker/ollama-proxy/`) fronts the sandbox Ollama on `:11435`: bearer token
  required, inference + read-only endpoints only (pull/delete/push refused), Ollama
  itself stays on loopback. The token lives only in KeePass (`SIP_Ollama_Proxy_Token`);
  the proxy and the backend both read it from there. New settings `OLLAMA_API_KEY`,
  `OLLAMA_CRED_KEY`, `OLLAMA_KEEPASS_URL`; `/api/v1/chat/status` reports
  `unauthorized` when the proxy rejects the token. Prod follows after dev checks out.
- **Unified array health** — every array now reports one of four statuses:
  **Healthy** (green), **Warning** (yellow), **Critical** (red), **Offline** (gray,
  replacing "unknown"), with a short reason in the status tooltip (new
  `metrics_current.health_detail`). Each vendor's metrics collector rolls up its
  documented current-state endpoints: Pure controllers/hardware/drives (REST 2.x);
  ONTAP node health, node state + FRUs, broken disks, shelf errors, HA
  takeover-possible and health-monitor alerts (the checks NetApp Harvest runs);
  HPE node membership, failed capacity and port link state; Hitachi
  `components/instance` (VSP F900); Oracle problems, chassis, cluster takeover and
  pool state; Dell Unity system + storage-processor health; StorageGRID grid health
  + disconnected nodes. Offline = no fresh collection for
  `HEALTH_OFFLINE_AFTER_SECONDS` (default 30 min), and enabled arrays that have
  never reported now appear as Offline instead of being missing. Alerts are not an
  input — four vendors' alert feeds are event logs and would keep a fixed fault red
  for days. Dashboard status pills, filter and sort use the four states.
- **Hardware wireframe** — a new **Hardware** tab in the array drilldown draws the
  array's physical layout: one card per enclosure (chassis, controller/node,
  shelf, cage, drive box, DPE/DAE, grid site) with every drive bay, port (grouped
  by protocol), controller, PSU, fan, battery, DIMM/CPU and module coloured by
  health — green/yellow/red, gray for unknown, dashed outline for empty bays and
  unused ports — plus per-type counts, a hover details panel (capacity, speed,
  model, mode…), a "needs attention" list and an issues-only highlight. Built from
  each vendor's hardware endpoints (Pure `hardware`/`drives`, ONTAP nodes+FRUs /
  shelves+bays / disks / ports, HPE `/disks` cages + `/ports`, Hitachi
  `components/instance`, Oracle chassis details + NICs, Unity DPE/DAE/disk/port/
  PSU/fan/LCC/IOM/memory/battery, StorageGRID `node-health`). Collected every
  metrics cycle into the new `hardware_inventory` table; served at
  `GET /arrays/{name}/hardware`.
- **backup-ops** (`jobs/backup-ops-mockup/`) — a standalone, on-network **Backup
  Operations** view for SIP (the platform's first Backup Operations capability):
  KPI tiles, a job-status donut, a jobs-by-workload panel, and a
  filterable/sortable job table. A small Python read-only web app (stdlib HTTP
  server) serving the page plus `/api/jobs` — **live, SELECT-only** queries over
  `stormart.cvlt_backupjobsummary`. Runs in the prod namespace
  `storage-mgt-tools-na` next to `cvlt-backup-summary` (the only place `stormart`
  is reachable), reusing that pipeline's Postgres creds read-only. Separate from
  the SIP app: own image `ghcr.io/apythoneer/backup-ops-mockup`, own path-filtered
  CI, own manual deploy on the `eks-prod` runner, exposed at
  `backup-ops.eks-cm-svcs-prod-ue2.aws.corp.intranet` so it can be demoed on the
  corp network (claude.ai is blocked). The job table includes a **Job ID** column;
  the KPI row carries a **Data Written** tile (`sum(media_size_gb)`) and there is an
  **Average Dedupe Savings %** line chart (`avg(dedup_savings_pct)` bucketed hourly
  in the 24h view, daily otherwise). The Commvault `*_gb` columns are byte-scaled
  (confirmed with PJ), so all data sizes are formatted from bytes.
- **cvlt-backup-summary service** (`jobs/cvlt-backup-summary/`) — standalone,
  separate from the SIP app. Containerises PJ's Commvault backup-job pipeline as a
  nightly `CronJob` (Commvault report → AWS RDS `stormart` Postgres → Datadog
  failure/resolution events; ships suspended until validated) plus an always-on
  health service exposing `/`, `/health`, `/status`, `/ping` with live
  Commvault/Postgres/Datadog connection checks and last-run/uptime. Own image,
  path-filtered CI, and a manual deploy workflow on the `eks-prod` runner.
  Temporary ownership — handoff plan in `docs/cvlt-backup-handoff.md`.
  Credentials use a self-renewing Commvault `token`+`refresh_token` pair persisted
  on a writable PVC (`cvlt-backup-var`), seeded once from a Secret by an
  initContainer — the script renews and rewrites it in place on each 401.
- **Chatbot data dictionary (`usm_dictionary`)** — foundation for the chatbot
  performance program (SCPSB-946, item 1 "tag the data"). A new additive
  `USM.usm_dictionary` table + a git-versioned seed
  (`app/services/dictionary.py`) describing core tables/columns with units and
  the synonyms users actually type. `build_system_prompt()` can inject the
  most-relevant entries as schema-linking context, gated behind
  `CHAT_USE_DICTIONARY` (**default off** — no prompt change until an eval set
  confirms no regression to forecasting/analytics). Retrieval is keyword-based
  today and is the drop-in seam for pgvector cosine similarity after the
  SCPSB-1022 Postgres cutover.

### Added
- **cvlt-backup-summary credential rotation** — `deploy-cvlt-backup.yml` gained a
  `reseed` input that recreates the `cvlt-backup-secrets` Secret from a freshly
  updated `CVLT_SECRETS_JSON` and wipes the PVC so the init container re-seeds the
  new token+refresh pair (the PVC otherwise keeps the old file). Needed because a
  stale seed pair fails Commvault auth (401 on report, 500/errorCode 101 on renew).

### Changed
- **Renamed SIP → ORBITS** (Observability & Reporting for Backup, Infrastructure,
  Telemetry & Storage). New name in the UI (logo, header, page title, dashboard), the
  API title, `APP_NAME`, the README and the docs. New addresses on the platform wildcard
  DNS and certificate: `orbits.eks-cm-svcs-nonprod-ue2.aws.corp.intranet` (dev) and
  `orbits.eks-cm-svcs-prod-ue2.aws.corp.intranet` (prod). The old `sip.…` hosts stay on
  the ingress and the frontend nginx answers them with a **308 redirect** to the same
  path on `orbits.…`, so bookmarks, links and API clients keep working (308 keeps POST
  bodies). Internal names are unchanged: `usm-*` images/services, the `USM` schema,
  namespaces, `app.kubernetes.io/part-of: sip` labels (Deployment selectors can't
  change in place), KeePass entries and the `sip_` API-key prefix. The dev nginx config
  also gains the unbuffered `/api/v1/chat/stream` location it was missing.
- **Storage AI: answers finish inside the 120 s timeout** — free-form Text-to-SQL
  skips the SQL self-correction call after `CHAT_FIX_BUDGET_S` (50 s) and the LLM
  narration after `CHAT_NARRATE_BUDGET_S` (80 s), instead of running on after the
  client has given up and holding a chat slot. The sandbox Ollama now runs with
  `OLLAMA_NUM_PARALLEL=2` and `OLLAMA_CONTEXT_LENGTH=8192`. The ~4.1k-token SQL
  prompt had filled the 4k default, so the prompt cache never worked: reading the
  prompt took 73 s cold and now takes 0.1–12 s cached.
- **cvlt-backup-summary schedule moved to hourly daytime** — the CronJob now runs
  `0 7-15 * * *` in `America/Denver` (07:00–15:00 Mountain, 9 runs/day) instead of
  once at 13:00 UTC. Backups run overnight where failures are expected and
  auto-retry; evaluating/ticketing then is noisy, so the pipeline runs across the
  settled business day. Pinned to the window's own timezone so Kubernetes handles
  MST/MDT. (Hourly re-ingests the same 24h window each run — the
  `cvlt_backupjobsummary` INSERT must upsert on `job_id`; flagged for PJ.)

### Fixed
- **Storage AI: capitalised questions missed the intent router, and EOSL/retired
  questions got wrong SQL** — the embedding is case-sensitive, so "any arrays past
  EOSL?" scored 0.76 instead of 0.95 and fell through to Text-to-SQL. That SQL filtered
  on a non-existent `disposition = 'Retired'` and answered "no results", when 2 active
  arrays are past EOSL. Questions are now lower-cased before routing (routing eval: 36 →
  38 of 52 right, still 0 wrong). The dictionary and schema prompt list the real
  `disposition` values (`Current` / `Decomming` / `Decommissioned`) and the
  `monitoring_status` values, and state that "slowest" means highest latency.
- **Pure arrays never collected on Kubernetes (0/25 "Authentication failed")** —
  `PureClient` connected to the bare array name (`https://psompa03b/…`), which
  only resolves where the resolver searches `corp.intranet` (the on-prem host).
  It now tries the array's FQDN, then management IP, then the name — the same
  fallback the NetApp/StorageGrid clients use — from `managed_arrays`, and stops
  at once if the array rejects the API token.
- **Array health flip-flopped and Offline was timezone-dependent** — two collector
  instances write the same `metrics_current` rows (on-prem in MST, cloud pods in
  UTC, the latter still on older code), so `array_status` was overwritten with
  the old vocabulary every cycle, and `collected_at` (a naive local timestamp)
  made a UTC API see every MST-written row as 7 h old → Offline. Health now uses
  `health_state` + `health_checked_at`, written only by the current collector and
  stamped on the **database clock** (`SYSUTCDATETIME()`), compared with the DB
  clock at read time. Rows not yet checked fall back to the old fields with a
  timezone-tolerant age.
- **Array health was hardcoded or never read** — HPE, Oracle and Hitachi always
  wrote `healthy`; NetApp called `/api/cluster/nodes` without `fields`, so node
  health was never returned and fell through to `healthy`; Dell mapped Unity
  `UNKNOWN` (0) to healthy and `MINOR` (15) to critical; Oracle silently skipped
  degraded/faulted pools. All now derive status from live component state (see
  *Unified array health* above).
- **cvlt-backup-summary ETL crashed on every run after the first** — the credential
  Secret mounts read-only (`0400`) and the init container's `cp` preserves that
  mode, so `secrets.json` lands on the PVC read-only. The pipeline script backs it
  up to `secrets.json.bak` at import time; the first run creates `.bak` fresh (fine)
  but it inherits `0400`, so the second run's `shutil.copy` can't overwrite the
  read-only `.bak` and the job dies with `PermissionError` before doing any work
  (and a token renewal, which rewrites `secrets.json`, would fail the same way).
  Only surfaced now because every prior successful run was the first on a freshly
  seeded PVC. The `seed-secrets` init container now `chmod 0600`s `secrets.json`
  (and any existing `.bak`) every start. Without this the new hourly CronJob would
  succeed once then fail the rest of the day.
- **cvlt-backup-summary health endpoint 500'd once a run was recorded** — `last_run`
  surfaces `EXTRACT(EPOCH …) AS duration_s`, a psycopg2 `Decimal`, which
  `json.dumps` can't serialize; `/health` therefore threw before responding and the
  health pod stayed `0/1` NotReady (blocking the deploy) the moment the first row
  landed in `cvlt_job_runs` — not a credential/secret problem. Cast `duration_s` to
  float and added a `json.dumps` default that coerces `Decimal`/`datetime` so the
  health endpoint can never 500 on serialization again.
- **cvlt-backup-summary reseed deadlocked on the PVC** — the reseed PVC delete hung
  on the `kubernetes.io/pvc-protection` finalizer (leftover completed/failed job
  pods still referenced the claim), which `|| true` masked; `apply` then couldn't
  recreate the `Terminating` claim, and the bound-check passed falsely because a
  `Terminating` PVC still reports `phase=Bound`. The reseed step now deletes the
  pinning Jobs/pods first, waits for the PVC to actually disappear (failing loudly
  if it can't), and the bound-check also requires an empty `deletionTimestamp`.
  Added `ttlSecondsAfterFinished: 1800` to the Job template so finished pods
  self-clean (also curbs orphaned pods), and raised the health rollout timeout to
  240s for slow ADOT-sidecar pod termination.
- **cvlt-backup-summary ETL pod blocked by the namespace ResourceQuota** — the
  `seed-secrets` initContainer had no `resources` block, so `storage-mgt-tools-na-rq`
  rejected pod creation (`must specify limits.memory for: seed-secrets`) and the
  Job never got a pod (which also left the PVC without a first consumer). Added a
  tiny requests+limits block to the initContainer. This was the actual blocker
  behind the ETL never running.
- **cvlt-backup-summary PVC moved from EBS to EFS** — `ebs-sc`
  (`WaitForFirstConsumer`) never bound on the prod cluster, so the nightly ETL
  pod sat `Pending` until the Job's `activeDeadlineSeconds` killed it
  (`DeadlineExceeded`) and no run ever completed. The PVC now uses
  `efs-prometheus-sc` (EFS access-point, `Immediate` binding); `deploy-cvlt-backup.yml`
  auto-migrates an existing unbound `ebs-sc` PVC and verifies the PVC is `Bound`
  before proceeding.
- **Prod-deploy ghcr image-provenance guard** (`deploy-prod.yml`) — the check now
  sends the OCI/Docker manifest `Accept` media types (ghcr serves a buildx OCI
  image index, which a bare request can't read) and is non-fatal, so it can't
  false-block a promotion whose images actually exist.

### Changed
- **Azure collector uses the on-prem USM KeePass directly** — the `azure-collector`
  overlay no longer deploys an in-AKS `keepass-broker` or needs `keepass-kdbx` /
  `keepass-master` secrets; the collector's `KEEPASS_URL` points at
  `usodclpsandadm1:2000` (reachable from AKS pods). Removes the Krunal-secret
  dependency for the Azure satellite.

### In progress
- **Chatbot performance program** — a data-dictionary / semantic layer + pgvector
  RAG (SCPSB-946), explicitly guard-railed to not affect capacity forecasting or
  analytics that share the model/DB (`docs/chatbot-performance.md`).
- **SQL Server → AWS Postgres:** application repoint (driver + SQL dialect +
  `sslmode=require`) + cutover; connectivity to `stormart01p` is proven from pods.
- Stable ingress for the prod/dev dashboards (currently port-forward).

---

## [3.5.0] — 2026-10-13 — Multi-CSP deployment & local-only chat

### Removed
- **DGX chat backend (POC)** — removed the off-network DGX / `qwen3:30b` second
  chat backend and its Cloudflare Access wiring from the chat service, API,
  frontend toggle and compose (`CHAT_DGX_*`, `CF_ACCESS_*`). It was a benchmark /
  POC; chat is now **local-only** (`qwen2.5:3b` via Ollama), so no chat path leaves
  the host.

### Fixed
- **Frontend crash-loop on the rootless nginx image** — the `nginx-unprivileged`
  switch (#68) `COPY`'d the config as root, so the uid-101 runtime user could not
  read `/etc/nginx/conf.d/default.conf` (`[emerg] Permission denied`), crash-looping
  the container. Fixed with `COPY --chown=nginx:nginx`. Surfaced on the first real
  run of that image (the on-prem sandbox deploy).

### Added
- **AWS prod deployment (EKS `c-5zsl4`)** — `deploy/overlays/prod` is the **full
  app** (backend + collector + frontend) on the AWS backbone, namespace
  `storage-mgt-tools-na`, deployed by `deploy-prod.yml` on `prod`-branch promotion
  (guards that the image exists in ghcr first), gated behind a `production` GitHub
  Environment (required-reviewer approval).
- **In-cluster prod runner** (`deploy/runner/eks-prod-runner.yaml`) — a self-hosted
  GitHub Actions runner (`eks-prod`) deployed as a pod in the dev EKS cluster — the
  only vantage point that reaches both GitHub and the prod Rancher (the nonprod EC2
  is firewalled off prod). Mounts the prod kubeconfig; PAT-registered. Bring-up
  runbook: `docs/SIP-prod-runner-setup.md`.
- **Azure collector satellite (AKS)** — `deploy/overlays/azure-collector` runs the
  **collector only** on AKS `c-jmkcx` (the 18 Azure arrays are reachable only from
  inside Azure), writing back to the central DB. Collector + in-namespace
  `keepass-broker` are hardened for Azure Policy / Gatekeeper (non-root,
  readonly-rootfs, drop caps, no SA-token automount; verified via server-side
  admission dry-run). Deployed by the `azure-prod` runner
  (`.github/workflows/deploy-azure-collector.yml`), which preflights the
  `keepass-kdbx`/`keepass-master` secrets. Needs those secrets in the namespace.
- **Deployment design of record** — `docs/SIP-deployment-architecture.md`: AWS is
  the backbone (dev + prod, full app); AKS is an Azure-collector-only satellite;
  GCP arrays are collected from AWS (no GCP namespace).

### Changed
- **Frontend image is now rootless** (`frontend/Dockerfile` →
  `nginxinc/nginx-unprivileged`) so a single image passes the AKS readonly-rootfs
  policy; drop-in for on-prem compose and dev EKS (both already bind 8080).

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
