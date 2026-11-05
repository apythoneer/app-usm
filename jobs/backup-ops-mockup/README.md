# backup-ops-mockup

A standalone, on-network **Backup Operations** view for SIP — the first Backup
Operations capability for the platform. It exists because the design artifact lives
on `claude.ai`, which the corp network blocks; this serves the same view from a
corporate URL.

It is **not** the SIP app and not wired into `deploy/overlays/*`. One small
read-only web pod: a Python stdlib HTTP server (`app.py`) that serves one page and a
JSON API over `stormart.cvlt_backupjobsummary`.

## Live data

It runs in the **prod** namespace (`storage-mgt-tools-na`) next to
`cvlt-backup-summary`, because that is the only place the `stormart` RDS is
reachable. It reuses that pipeline's `cvlt-backup-secrets` Secret (the `postgres`
block only, mounted read-only) and issues **SELECT-only** queries.

- `GET /` — the page (`index.html`): KPI tiles (incl. **Data Written** =
  `sum(media_size_gb)`), a job-status donut, a jobs-by-workload panel, an
  **Average Dedupe Savings %** line chart (`avg(dedup_savings_pct)` over time), and a
  filterable/sortable job table with a **Job ID** column.
- `GET /api/jobs?window=<days>&client=<substr>&status=<csv>&limit=<n>` — aggregates
  (totals, status breakdown, by-workload, data written, and an avg-dedupe time series
  bucketed hourly for 24h / daily beyond) plus the most recent rows for the current
  filters. `window` is clamped 1..90; everything is parameterized.
- `GET /healthz` — liveness (no DB call); used by the probes.

The page fetches `/api/jobs` on load and on every filter change (window / client /
status), so all numbers are live.

> Note on the `*_gb` columns: `size_of_application_gb` and `media_size_gb` are
> **byte-scaled despite the name** (confirmed with PJ, 2026-11). The page scales from
> bytes (`fmtBytes`) for the data-size tiles and the table "Data" column.

## Deploy

- **Image:** `ghcr.io/apythoneer/backup-ops-mockup` — built by
  `.github/workflows/ci-backup-ops.yml` on pushes to `main` touching this directory
  (tags `:<sha>`, `:latest`).
- **Target:** prod ns `storage-mgt-tools-na`, via
  `.github/workflows/deploy-backup-ops.yml` (manual `workflow_dispatch`, on the
  `eks-prod` in-cluster runner — the only vantage that reaches prod Rancher and
  stormart). The deploy verifies `/api/jobs` actually returns data from inside the
  pod before finishing.
- **URL:** <https://backup-ops.eks-cm-svcs-prod-ue2.aws.corp.intranet> — on the
  `*.eks-cm-svcs-prod-ue2.aws.corp.intranet` wildcard, TLS from the default ingress
  controller cert (same recipe as the SIP ingress).

```
# after CI has built an image from main:
#   Actions → "Deploy backup-ops (AWS EKS prod)" → Run workflow → image_tag = latest (or a SHA)
```

## Teardown

```
kubectl -n storage-mgt-tools-na delete -k jobs/backup-ops-mockup/k8s
```

Nothing else references these objects (the `cvlt-backup-secrets` Secret is owned by
the pipeline and is left intact), so the delete is clean.

> The earlier **dev** instance (static, sample data, in `storage-mgt-tools-na-dev`)
> is superseded by this live prod app and can be removed with
> `kubectl -n storage-mgt-tools-na-dev delete deploy/svc/ingress backup-ops-mockup`.
