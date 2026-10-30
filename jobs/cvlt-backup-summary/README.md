# cvlt-backup-summary

Standalone nightly job (PJ's `dataDogIncidentManagement.py`), containerised and
run as a Kubernetes `CronJob`. **Kept separate from the SIP app**: own image
(`ghcr.io/apythoneer/cvlt-backup-summary`), own CI (`.github/workflows/ci-cvlt-backup.yml`,
path-filtered), own manual deploy (`.github/workflows/deploy-cvlt-backup.yml`, reuses the
`eks-prod` runner), own manifests here. It is **not** wired into `deploy/overlays/*`.

> **Ownership:** hosted here temporarily (a personal kubectl identity can't write to the
> namespace). Hands off to PJ/Krunal's pipeline — see
> [docs/cvlt-backup-handoff.md](../../docs/cvlt-backup-handoff.md).

## What it does
1. Pulls the last 24h Commvault backup-job report from the Command Center
   ReportsPlus API on `uscomvlt1.corp.intranet`.
2. Upserts rows into `stormart.cvlt_backupjobsummary` (Postgres), idempotent
   (`ON CONFLICT (data_source, job_id) DO NOTHING`).
3. Posts Datadog **events** for newly-failed checks and resolution events when a
   check recovers; state is tracked in `stormart.cvlt_datadog_events`.
   (It posts Datadog events — it does NOT call ServiceNow directly; any SNOW
   ticket is raised downstream by a Datadog monitor/webhook.)

## Components
- **CronJob** `cvlt-backup-summary` — nightly ETL (runs `run_job.py`, which wraps
  PJ's `main()` and records each run in `stormart.cvlt_job_runs`).
- **Deployment + Service** `cvlt-backup-summary-health` — always-on health server
  (`healthserver.py`) on `:8080`. This is the "service header" to validate health,
  connections, and uptime:
  - `GET /`        — HTML status page (state badge, per-connection OK/FAIL, last run, uptime)
  - `GET /health`  — 200 healthy / 503 if a *critical* connection is down (readiness)
  - `GET /status`  — full JSON (checks + last_run + uptime), always 200
  - `GET /ping`    — liveness (no dependency calls)

  Live connection checks: **Commvault** TCP 443, **Postgres** `SELECT 1` on the RDS
  stormart DB, **Datadog** `GET /api/v1/validate` (proves key + SaaS egress). Commvault
  and Postgres are critical (down ⇒ `unhealthy`); Datadog down ⇒ `degraded`.

  View it locally:
  ```
  kubectl -n storage-mgt-tools-na port-forward svc/cvlt-backup-summary-health 8080:8080
  # then open http://localhost:8080/  (or curl http://localhost:8080/status)
  ```

## Token persistence (the key design point)
The script now carries its own auth: it reads `commvault.token` **and**
`commvault.refresh_token` from secrets.json and, on a 401, calls
`/commandcenter/api/V4/AccessToken/Renew`, then **rewrites secrets.json** with the
new pair (and writes a `.bak` on every import). Because the job runs daily the
access token is essentially always expired at run time → it renews every run, and
the **refresh_token rotates** each time. That rotating pair must survive pod
restarts, so:

- `/home/cvadmin/.var` is a **writable PVC** (`cvlt-backup-var`, see `pvc.yaml`),
  **not** a read-only Secret mount — the script owns the file and keeps it current.
- An **initContainer** (`seed-secrets`) copies secrets.json from the Secret into
  the PVC **once, only if absent**, so it never clobbers a token the script has
  since renewed. The Secret is just the bootstrap seed; the PVC is the source of truth.
- The **health Deployment does not mount the PVC** — it mounts the read-only Secret,
  because it only needs the (static) Postgres/Datadog creds and never renews the token.

## Staged — what gates "fire it up"
The CronJob ships with `suspend: true`. Prod lives on AWS EKS `c-5zsl4`, whose
Rancher API is reachable **only from the in-cluster `eks-prod` runner** (a pod in
the dev cluster — the nonprod EC2 and laptops cannot reach it, same as SIP's
`deploy-prod.yml`). So activation goes through the `deploy-cvlt-backup.yml`
workflow, not hand-run kubectl.

### 1. Set the seed creds as an Actions secret
`secrets.json` can't be `kubectl`-applied from a laptop/EC2 (no prod reachability),
so the runner creates the k8s Secret from a repo Actions secret. Set
**`CVLT_SECRETS_JSON`** (repo → Settings → Secrets and variables → Actions) to a
**FRESH** `secrets.json` (`k8s/secret.example.yaml` shows the shape) — minted right
before activation, because a stale `refresh_token` 401s on the first renew. The
workflow creates the Secret from it **once** and never overwrites (the script owns
the renewed pair on the PVC thereafter).

### 2. Run the deploy workflow (health first)
`deploy-cvlt-backup.yml` → **Run workflow**: `image_sha=<main SHA>`, `run_etl=false`.
It seeds the Secret, applies the manifests (PVC + CronJob *suspended* + health), and
brings up the health server.

### 3. Confirm egress on the health page (three legs) — see below.

### 4. Enable the nightly ETL
Re-run the workflow with `run_etl=true` once the health page is green (esp.
Datadog). That patches `suspend:false`. To smoke-test a run immediately without
waiting for 13:00 UTC (from a prod-reaching vantage):
```
kubectl -n storage-mgt-tools-na create job --from=cronjob/cvlt-backup-summary cvlt-smoke-1
kubectl -n storage-mgt-tools-na logs job/cvlt-smoke-1 -f
```

## Egress — the real open question ("where does it execute")
The pod needs to reach **three** destinations; they fail independently:

| Leg | Target | Confidence |
|-----|--------|------------|
| Commvault (on-prem) | `uscomvlt1.corp.intranet:443` | **Proven** — Krunal's sibling `storage-ops-commvault-*` jobs in this same namespace complete `1/1` daily pulling this exact Commvault. |
| Postgres (AWS cloud) | `aws-pgsql-…us-east-2.rds.amazonaws.com:5432` | **Proven** — same sibling jobs write `stormart` daily (AWS RDS, priv IP `10.168.203.75` in the prod VPC — not on-prem). |
| Datadog (SaaS) | `api.us5.datadoghq.com:443` | **Unverified — the one open leg.** Needs public egress, which an egress-restricted cluster may block. The health page's `/status` confirms it once the Deployment is up (`GET /api/v1/validate`). |

Verify from a throwaway pod in the target namespace before flipping `suspend`:
```
kubectl -n storage-mgt-tools-na run egress-test --rm -it --restart=Never \
  --image=ghcr.io/apythoneer/cvlt-backup-summary:latest \
  --overrides='{"spec":{"imagePullSecrets":[{"name":"ghcr-apythoneer"}]}}' \
  --command -- python - <<'PY'
import socket
for host, port in [("uscomvlt1.corp.intranet",443),
                   ("api.us5.datadoghq.com",443)]:
    try:
        socket.create_connection((host, port), timeout=5).close()
        print(f"OK   {host}:{port}")
    except Exception as e:
        print(f"FAIL {host}:{port} -> {e}")
PY
```
(Add the Postgres host once known.) If the Datadog leg fails, options are a cluster
egress/NAT allow-list, routing DD posts through an existing proxy, or running the
job on a node/VM that already has the needed reachability.

## Code notes for later (not blockers)
- `send_failure_events` selects `from cvlt_backupjobsummary` unqualified while
  everything else is `stormart.`-qualified — relies on the role's `search_path`
  including `stormart`. Qualify it to be safe.
- TLS verification is disabled against Commvault (`verify=False`). Fine internally;
  revisit if a cert/CA becomes available.
- Renewal persistence depends on the PVC (`ebs-sc`, `WaitForFirstConsumer`). It
  stays `Pending` until the first CronJob pod schedules — that's normal, not a
  fault. If instead the first ETL pod is stuck `Pending`/`ContainerCreating`, check
  `kubectl -n storage-mgt-tools-na get pvc cvlt-backup-var` and its events for a
  provisioning/quota problem.
