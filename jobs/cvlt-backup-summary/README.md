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

## Staged — what gates "fire it up"
The CronJob ships with `suspend: true`. To go live:

### 1. Renewable Commvault token
The Commvault login was stripped from the script (`# REMOVED`); it uses a static
`ACCESS_TOKEN`. A static Authtoken **expires**, so a daily job will eventually
401. Before enabling, decide the auth story and put the result in the Secret:
- re-add a login step (QSDK `/Login`) that mints a fresh token each run, **or**
- provision a long-lived Commvault API key / service account token.

### 2. Create the Secret (out-of-band, never committed)
See `k8s/secret.example.yaml`. The script reads `/home/cvadmin/.var/secrets.json`
verbatim; the Secret is mounted there, so no code change.
```
kubectl -n storage-mgt-tools-na create secret generic cvlt-backup-secrets \
    --from-file=secrets.json=/tmp/secrets.json
```

### 3. Confirm egress (three legs) — see below.

### 4. Enable
```
# build happens on merge via ci-cvlt-backup.yml; pin the image SHA:
kubectl -n storage-mgt-tools-na set image cronjob/cvlt-backup-summary \
    cvlt-backup-summary=ghcr.io/apythoneer/cvlt-backup-summary:<merged-sha>
kubectl -n storage-mgt-tools-na patch cronjob cvlt-backup-summary \
    -p '{"spec":{"suspend":false}}'
# smoke-test immediately without waiting for 13:00 UTC:
kubectl -n storage-mgt-tools-na create job --from=cronjob/cvlt-backup-summary cvlt-smoke-1
kubectl -n storage-mgt-tools-na logs job/cvlt-smoke-1 -f
```

## Egress — the real open question ("where does it execute")
The pod needs to reach **three** destinations; they fail independently:

| Leg | Target | Confidence |
|-----|--------|------------|
| Commvault (on-prem) | `uscomvlt1.corp.intranet:443` | **Likely OK** — the SIP pods in this same cluster already reach on-prem `usidcvsql0252.ctl.intranet` (SQL Server), so corp on-prem routing exists. Different host though — verify. |
| Postgres (AWS cloud) | stormart `:5432` | Likely OK from the AWS cluster; confirm host + SG allows the node CIDR. |
| Datadog (SaaS) | `api.us5.datadoghq.com:443` | **Riskiest** — needs public internet egress, which an egress-restricted cluster may block. Must test. |

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
