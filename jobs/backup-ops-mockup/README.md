# backup-ops-mockup

A standalone, on-network showcase of the SIP **Backup Operations** view — the first
Backup Operations capability for the platform. It exists because the design artifact
lives on `claude.ai`, which the corp network blocks; this serves the same page from a
corporate URL so it can be demoed to PJ and others.

It is **not** the SIP app and not wired into `deploy/overlays/*`. One rootless-nginx
pod serving one static HTML page, with its own image, CI, and ingress.

## What it shows

`index.html` is a self-contained page (no build step, no external JS libraries — only
Google Fonts): KPI tiles, a job-status donut, a jobs-by-workload bar panel, and a
filterable/sortable job table. Filters (window / client / status) run live over a
**seeded sample dataset** generated in-page, so the numbers are stable across reloads
and are clearly labelled as sample data, not production figures.

The layout mirrors the real data model of `stormart.cvlt_backupjobsummary` (client,
workload, agent, start/end, duration, size, job_status, failure_reason) so it doubles
as the spec for the eventual SIP React page.

> Two data caveats are surfaced in the page itself: `size_of_application_gb` looks
> byte-scaled (units pending confirmation with PJ), and the figures are sample data.

## Deploy

- **Image:** `ghcr.io/apythoneer/backup-ops-mockup` — built and pushed by
  `.github/workflows/ci-backup-ops.yml` on pushes to `main` that touch this directory
  (tags `:<sha>` and `:latest`).
- **Target:** dev namespace `storage-mgt-tools-na-dev` on the AWS nonprod services
  cluster, via `.github/workflows/deploy-backup-ops.yml` (manual `workflow_dispatch`,
  on the `ec2-dev` self-hosted runner — the only vantage that reaches the nonprod
  Rancher API).
- **URL:** <https://backup-ops.eks-cm-svcs-nonprod-ue2.aws.corp.intranet> — on the
  `*.eks-cm-svcs-nonprod-ue2.aws.corp.intranet` wildcard, TLS from the default ingress
  controller cert (same recipe as the SIP dev ingress).

```
# after CI has built an image from main:
#   Actions → "Deploy backup-ops-mockup (dev)" → Run workflow → image_tag = latest (or a SHA)
```

## Teardown

```
kubectl -n storage-mgt-tools-na-dev delete -k jobs/backup-ops-mockup/k8s
```

Nothing else references these objects, so the delete is clean.
