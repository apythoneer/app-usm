# Backup Operations on SIP — build log, findings & runbook

_Last updated 2026-11-02._

The single entry point for the **Backup Operations** capability: the Commvault →
`stormart` pipeline and the on-network view that reads it. Start here, then drill
into the per-service READMEs and the handoff doc linked below.

- Pipeline: [`jobs/cvlt-backup-summary/`](../jobs/cvlt-backup-summary/) · [README](../jobs/cvlt-backup-summary/README.md) · [handoff plan](cvlt-backup-handoff.md)
- View: [`jobs/backup-ops-mockup/`](../jobs/backup-ops-mockup/) · [README](../jobs/backup-ops-mockup/README.md)

---

## 1. What exists now

Two standalone services, both **separate from the SIP app**, both in the prod
namespace `storage-mgt-tools-na`:

| Service | What it does | Shape |
|---|---|---|
| **cvlt-backup-summary** | Pulls the Commvault backup-job report → `stormart.cvlt_backupjobsummary` → Datadog failure/resolution events. Plus an always-on health surface. | A `CronJob` (hourly) + a health `Deployment`/`Service`. Image `ghcr.io/apythoneer/cvlt-backup-summary`. |
| **backup-ops** | The live **Backup Operations** web view: KPIs, status donut, jobs-by-workload, filterable job table. | One read-only web pod serving a page + `/api/jobs` (SELECT-only over `stormart`). Image `ghcr.io/apythoneer/backup-ops-mockup`, ingress `backup-ops.eks-cm-svcs-prod-ue2.aws.corp.intranet`. |

Both are live in prod as of 2026-11-02. The pipeline runs hourly 07:00–15:00
Mountain; the view queries `stormart` live.

---

## 2. Where things run and how to reach them (the vantage map)

This is the part that cost the most time to work out — keep it handy.

- **Prod** = AWS EKS, namespace `storage-mgt-tools-na` on the `cm-svcs-prod` services
  cluster. It is reachable **only** from the in-cluster `eks-prod` self-hosted GitHub
  Actions runner. A laptop or the nonprod EC2 **cannot** reach prod Rancher — all prod
  `kubectl` goes through a `workflow_dispatch` on `eks-prod`.
- **Dev** = namespace `storage-mgt-tools-na-dev` on the `cm-svcs-nonprod` cluster,
  reachable from the `ec2-dev` self-hosted runner (which has `kubectl` on PATH; the
  `eks-prod` runner instead gets it via an initContainer at `/tools`, so prod deploy
  workflows start with a "put kubectl on PATH" step).
- **`stormart`** = AWS RDS `stormart01p`
  (`aws-pgsql-storage-mgt-tools-na-p-use2-01…rds.amazonaws.com`, priv IP in the prod
  VPC), schema `stormart`. **Only reachable from the prod cluster.** That is why the
  live view had to run in prod, not dev.
- **Ingress** = class `nginx`, host on the services-cluster wildcard
  (`*.eks-cm-svcs-{nonprod,prod}-ue2.aws.corp.intranet`). TLS is **host-only with no
  `secretName`** (the platform's default controller wildcard cert), and
  `nginx.ingress.kubernetes.io/ssl-redirect: "false"` is **required** (TLS terminates
  at the edge ELB and is forwarded as HTTP; without it ingress-nginx 308-loops). The
  `cm-wklds-*` wildcard points at a different cluster and 404s — always use `cm-svcs-*`.
- **claude.ai is blocked on the corp network** — that is the whole reason the view is
  self-hosted behind corp ingress instead of a shared artifact link.

---

## 3. Hard-won gotchas (the stuff that broke, and the fix)

Each of these cost a diagnosis cycle. If something's wedged, check here first.

| Symptom | Cause | Fix |
|---|---|---|
| ETL pod stuck `Pending`, then `DeadlineExceeded` | `ebs-sc` is `WaitForFirstConsumer` and never binds on this cluster | Use EFS: `efs-prometheus-sc` (`efs-ap`, `Immediate`) — binds in ~1s. Deploy auto-migrates an old `ebs-sc` PVC. |
| Pod never created: `must specify limits.memory for: <container>` | Namespace `ResourceQuota` requires cpu **and** memory **requests and limits** on **every** container, including initContainers | Put a full `resources` block on every container. |
| Reseed hangs; PVC won't delete/recreate | A finished Job pod still references the PVC (`pvc-protection`); a `Terminating` PVC still reports `phase=Bound` | Delete pinning Jobs/pods first, wait until the PVC is actually gone, and check `deletionTimestamp` is empty (not just phase). `ttlSecondsAfterFinished: 1800` keeps finished pods from pinning it. |
| Health pod stuck `0/1`, deploy blocked | `/health` 500s: `EXTRACT(EPOCH…)` returns a `Decimal`, which `json.dumps` can't serialize — only triggers once a run row exists | Cast to `float` + a `json.dumps` default that coerces `Decimal`/`datetime`. |
| ETL crashes on the **2nd+** run only: `PermissionError … secrets.json.bak` | The Secret mounts `0400`; `cp` preserves that mode, so `secrets.json` lands read-only. The script copies it to `.bak` at import; the first run creates `.bak` fresh (ok) but it inherits `0400`, so the next run can't overwrite it | Init container `chmod 0600` on `secrets.json` (and any `.bak`) every start. |
| CronJob keeps coming back **suspended** after a deploy | `apply -k` re-applies `suspend: true` from the manifest every time | Pass `run_etl=true` to the deploy — it patches `suspend:false` after the apply. |
| A deploy "verified" but tested nothing | `kubectl exec POD -- python -` **without `-i`** discards stdin; the heredoc never reaches the pod, python runs empty, exits 0 | Use `kubectl exec -i`. |
| GitHub rejects a PR body ("Excess Sensitive Detail") | Dense internal infra detail (hostnames, cluster, namespace, runner labels) in an externally-posted body | Keep PR bodies lean; put the topology in the repo (READMEs/this doc), not the PR body. |

---

## 4. Data findings — `stormart.cvlt_backupjobsummary`

What we learned about the table the view reads:

- **Conflict handling:** the pipeline INSERT ends with
  `ON CONFLICT (data_source, job_id) DO NOTHING`. There is a **unique key on
  `(data_source, job_id)`**, so re-pulling the same 24h window every hour is a safe
  no-op — **no duplicate rows, no errors**. This is what makes the hourly cadence
  safe. _Nuance:_ `DO NOTHING` means a row is written once and **never updated**, so a
  job whose status/size changes after first capture won't be refreshed (fine for our
  daytime-settled window; would need `DO UPDATE` otherwise). _(Confirmed with PJ +
  code, 2026-11-02.)_
- **The `*_gb` columns are byte-scaled, not GB.** `size_of_application_gb` and
  `media_size_gb` hold **bytes** despite the name (magnitudes in the billions).
  The backup-ops view scales from bytes (`fmtBytes`) for the "Data protected" /
  "Data Written" tiles and the table "Data" column. _(Confirmed with PJ, 2026-11-05.)_
- **`client_name` is very high-cardinality** — ~8,759 distinct values in ~13,360 jobs
  over 24h (almost one per job). It likely is **not** the clean client identity (may
  carry per-subclient/instance detail), which would inflate the "Clients" KPI. Open
  question: is `client` the canonical field for the UI? If so it's a one-line query
  change.
- **Volume / statuses:** ~13k jobs per rolling 24h. Statuses observed: `Completed`,
  `Failed`, `Completed with errors` (schema also allows `Committed`, `Partial Success`,
  `Running`). Overwhelmingly `Completed`.
- **Shared / append-across-time:** row count spans weeks (152k+), far more than one
  24h pull — another writer (Krunal's `storage-ops`?) almost certainly co-populates it.
  Ownership not yet confirmed.
- **Backups run overnight and are allowed to fail/retry**; the pipeline deliberately
  runs in the settled **daytime** window so it reports final state, not transient
  overnight churn.

---

## 5. Runbook

All prod actions go through `workflow_dispatch` (Actions tab). Nothing is reachable
by `kubectl` from a laptop.

**Deploy / update the pipeline** — `Deploy cvlt-backup-summary (AWS EKS)`:
- `image_sha`: a SHA from `ci-cvlt-backup.yml`, or `latest`.
- `run_etl=true`: keep the CronJob unsuspended (required every deploy, see §3).
- `smoke=true`: run a one-off ETL job now and show its logs.
- `inspect=true`: dump what's captured in `stormart` (row counts, latest jobs).
- `diag=true`: dump PVC/pod/event state.
- `reseed=true`: **rotate creds** — update the `CVLT_SECRETS_JSON` Actions secret with
  a FRESH Commvault token+refresh pair first, then run with `reseed=true` (recreates
  the Secret and wipes the PVC so the new pair seeds; otherwise the stale PVC file wins).

**Deploy the view** — `Deploy backup-ops (AWS EKS prod)`:
- `image_tag`: a SHA from `ci-backup-ops.yml`, or `latest`.
- Reuses the pipeline's `cvlt-backup-secrets` (postgres block, read-only). The deploy
  **verifies `/api/jobs` returns data from inside the pod** before succeeding.
- URL: `https://backup-ops.eks-cm-svcs-prod-ue2.aws.corp.intranet`.

**Teardown the view** (safe; leaves the pipeline's Secret intact):
```
kubectl -n storage-mgt-tools-na delete -k jobs/backup-ops-mockup/k8s
```

**Credentials model (pipeline):** the script reads a Commvault `token`+`refresh_token`
pair, renews on a 401, and rewrites `secrets.json` **in place on a writable PVC**
(`cvlt-backup-var`, EFS). The `cvlt-backup-secrets` Secret is only the one-time seed;
the PVC is the living source of truth. A stale seed fails hard (401 on report → 500 /
`errorCode 101` on renew) — reseed with a fresh pair to recover.

---

## 6. Open questions for PJ / Krunal (status)

| # | Question | Status |
|---|---|---|
| 1 | Does the INSERT upsert on job_id or append? | ✅ Resolved — `ON CONFLICT (data_source, job_id) DO NOTHING`; hourly is safe. |
| 2 | `*_gb` column units (looks byte-scaled)? | ✅ Resolved — they're **bytes**; view scales from bytes. (2026-11-05) |
| 3 | Who else writes to `cvlt_backupjobsummary` (shared table ownership)? | ◻ Open — likely `storage-ops`. |
| 4 | Confirm 7am–3pm **Mountain** / hourly cadence. | ✅ Mountain confirmed (presence card). |
| 5 | Commvault access/refresh token TTLs + refresh-token expiry on idle? | ◻ Open — affects the renewal loop over quiet periods. |
| 6 | Egress plan/timeline for PJ's internal health/logging APIs (so SIP can consume them)? | ◻ Open. |
| 7 | How should SIP read backup data — DB view, our read-only API, or PJ's APIs post-egress? | ◻ Open — drives the next build. |
| 8 | A read-only DB user/role on `stormart` (vs reusing the pipeline's read-write cred)? | ◻ Open. |
| 9 | Canonical client field (`client` vs `client_name`) + full `job_status` value set + meaning of `Committed`/`Partial Success`? | ◻ Open — `client_name` cardinality suggests it's not the UI client. |

---

## 7. PR log (this effort)

| PR | What |
|---|---|
| #89 | PVC `ebs-sc` → EFS (`efs-prometheus-sc`, RWX) |
| #90 | `seed-secrets` initContainer resources (ResourceQuota fix — the real "ETL never ran" cause) |
| #91 | `reseed` deploy path (rotate creds from `CVLT_SECRETS_JSON`) |
| #92 | Reseed PVC-deadlock fix: delete pinning pods, `deletionTimestamp` check, `ttlSecondsAfterFinished`, 240s rollout |
| #93 | Health-rollout failure diagnostics |
| #94 | Health endpoint `Decimal` → float JSON fix |
| #95 | Read-only `inspect` of captured `stormart` data |
| #96 | Schedule → hourly 07:00–15:00 `America/Denver` |
| #97 | `secrets.json` perms fix (recurring-run `.bak` crash) |
| #98 | backup-ops showcase — static sample page in dev |
| #99 | backup-ops → **live** data, moved to prod behind ingress |
| #100 | Deploy verify fix (`kubectl exec -i` — the no-op check) |
