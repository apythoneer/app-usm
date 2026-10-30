# cvlt-backup-summary — ownership & handoff plan

## What this is
A standalone service wrapping PJ's `dataDogIncidentManagement.py` (Commvault
backup-job report → AWS RDS `stormart` Postgres → Datadog failure/resolution
events). It lives under [`jobs/cvlt-backup-summary/`](../jobs/cvlt-backup-summary/)
and is deliberately **separate** from the SIP app (own image, CI, deploy workflow,
namespace-scoped workloads).

| Piece | Where |
|-------|-------|
| Code + Dockerfile + manifests | `jobs/cvlt-backup-summary/` |
| Image | `ghcr.io/apythoneer/cvlt-backup-summary` |
| Build CI | `.github/workflows/ci-cvlt-backup.yml` (path-filtered) |
| Deploy | `.github/workflows/deploy-cvlt-backup.yml` (manual, `eks-prod` runner) |
| Runtime | ns `storage-mgt-tools-na` on `cm-svcs-prod-use2`: a health `Deployment`+`Service` and a nightly `CronJob` |
| Secret | `cvlt-backup-secrets` (created out-of-band; real Commvault/PG/Datadog creds) |

## Why we're hosting it temporarily (Option A)
A personal kubectl identity (`u-c3qec7qy7j`) has **no RBAC** to write CronJobs/
Services in `storage-mgt-tools-na` — only a privileged pipeline SA can. Rather than
block on new RBAC, we reuse SIP's proven `eks-prod` runner to deploy. This is a
**stop-gap**: the service belongs downstream with PJ/Krunal.

## Target owner
PJ (the task owner) / Krunal, whose `storage-ops-*` CronJobs (image
`ghcr.io/kplumen/storage_ops_jobs_commvault`) already run the sibling Commvault
jobs in this same namespace via their own deploy mechanism + KeePass-brokered creds
(`CommVault_scriptuser`, `postgres_db_creds`). That pipeline is the natural home.

## Handoff checklist (do when their K8s structure is ready)
- [ ] **Repo/image**: move `jobs/cvlt-backup-summary/` to their repo and publish the
      image under their registry (e.g. `ghcr.io/kplumen/...`); update manifest image refs.
- [ ] **Credentials**: current model is self-renewing — the script reads a
      `token`+`refresh_token` pair and renews on 401, rewriting `secrets.json`. The
      pair is seeded once from the `cvlt-backup-secrets` Secret into a **writable PVC**
      (`cvlt-backup-var`) that persists the rotating pair across runs. If moving to the
      **KeePass broker** (`http://keepass-broker:2000`, `CommVault_scriptuser` /
      `postgres_db_creds`) like the sibling jobs, note the pair rotates — the broker
      path needs write-back (or a login-per-run that mints a fresh pair), not just a read.
- [ ] **Deploy**: retire `deploy-cvlt-backup.yml` here; their pipeline/SA applies the
      manifests. Confirm their SA can manage CronJob/Deployment/Service in the ns.
- [ ] **CI**: retire `ci-cvlt-backup.yml` here once their build publishes the image.
- [ ] **Datadog eventing**: consider folding the DD failure/resolution step into the
      existing `storage_ops_jobs_commvault` framework (its `CV_PUBLISH` env looks like
      the toggle) instead of a parallel container re-pulling the same report.
- [ ] **Cleanup**: remove our workloads + Secret from the namespace after cutover.

## Known-good facts (verified 2026-10-29)
- Postgres is **AWS RDS** (`aws-pgsql-storage-mgt-tools-na-p-use2-01.czckywwqu124.us-east-2.rds.amazonaws.com`,
  priv IP `10.168.203.75` in the prod VPC), DB `stormart01p`, schema `stormart` — not on-prem.
- Commvault + Postgres egress + auth are **proven**: sibling `storage-ops-commvault-*`
  jobs complete `1/1` daily.
- **Datadog SaaS egress is the one unverified leg** — the health server's
  `/status` Datadog check (`/api/v1/validate`) confirms it once the Deployment is up.

## Open items before enabling the nightly ETL
1. Confirm Datadog egress green on the health page.
2. Seed `cvlt-backup-secrets` with a **fresh** Commvault `token`+`refresh_token`
   pair right before enabling (a stale refresh_token 401s on the first renew). The
   PVC persists the pair thereafter.
3. Confirm the PVC `cvlt-backup-var` is `Bound`.
4. Then deploy with `run_etl=true` (or patch `suspend:false`).
