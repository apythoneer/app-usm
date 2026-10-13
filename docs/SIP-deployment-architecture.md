# SIP — Deployment Architecture & CI/CD Design Sheet

_Design of record for how SIP is built, deployed and collects data across CSPs. 2026-10-12._
_Supersedes the environment model in `reports/SIP-Project-Summary.md` (which incorrectly labelled AKS as "prod")._

---

## 1. Principle

**AWS is the primary backbone.** The full application (frontend + backend + collector + DB) runs in AWS, for **dev** and **prod**, because AWS cross-connects to on-prem, to GCP arrays, and to the AKS pod network. **Azure (AKS) is a collector-only satellite** — it exists solely because the 18 Azure arrays are reachable *only* from inside Azure. There is **no GCP namespace**: GCP arrays are reachable from AWS, so they are collected from AWS.

```
                         ┌───────────────────────── AWS (primary backbone) ─────────────────────────┐
                         │                                                                           │
   on-prem arrays ──┐    │   EKS dev ns  (storage-mgt-tools-na-dev, c-lnk78)  = FULL APP (dev)        │
   AWS arrays ──────┼────┼─> EKS prod ns (storage-mgt-tools-na, <prod EKS>)   = FULL APP (prod)       │
   GCP arrays ──────┘    │        backend + collector + frontend ──> AWS Postgres (stormart01p)       │
                         │                                   ▲                                        │
                         └───────────────────────────────────┼────────────────────────────────────── ┘
                                                              │ writes (5432, proven reachable)
   Azure arrays ──> AKS ns (storage-mgt-tools-na, c-jmkcx) = COLLECTOR ONLY ──────────────────────────┘
                    (Azure is the only place that can reach Azure arrays)
```

---

## 2. Assets

| # | Asset | Concrete identity | Role |
|---|-------|-------------------|------|
| 1 | **EC2 (AWS)** | `awuse2lsanadmp01.aws.corp.intranet` (10.168.8.110) | CI/CD runner that deploys **AWS dev + prod** namespaces; corp-reachable to AWS Rancher |
| 2 | **Azure VM** | `azeus2lsanadmp1` (10.120.16.141) | CI/CD runner that deploys the **AKS Azure-collector** namespace; same VNet as AKS + Azure arrays |
| 3 | **GCP VM** | _name TBD_ | Admin box. **Not in the collection path** — GCP arrays are collected from AWS (role to confirm) |
| 4 | **Prod ns — Azure** | AKS `c-jmkcx` / `storage-mgt-tools-na` | **Azure collector only** (reaches the 18 Azure arrays) |
| 5 | **Prod ns — AWS** | EKS `<prod cluster id TBD>` / `storage-mgt-tools-na` | **Full app (prod)** |
| 6 | **Dev ns — AWS** | EKS `c-lnk78` / `storage-mgt-tools-na-dev` | **Full app (dev)** |
| 7 | **Postgres (AWS)** | `aws-pgsql-storage-mgt-tools-na-p-use2-01…rds.amazonaws.com:5432` (10.168.203.75), DB `stormart01p` | Future StorMart (central DB) |

---

## 3. Collection strategy (who scrapes what, from where)

Driven by the proven reachability matrix (§6). Each array is collected from the one place that can reach it; all writes land in the single AWS Postgres.

| Array location | Collected from | Why |
|---|---|---|
| On-prem | AWS (EKS) | AWS pods reach on-prem 1433 |
| AWS | AWS (EKS) | native |
| **GCP** | **AWS (EKS)** | AWS pods reach GCP arrays via transit → **no GCP namespace needed** |
| **Azure** | **Azure (AKS collector)** | AWS **cannot** reach Azure arrays (firewall); AKS pods can |

The AKS collector writes back to the AWS Postgres (proven reachable from AKS pods at 5432).

---

## 4. CI/CD pipeline

**Build once, promote the same artifact.** GitHub-hosted runners build + push images to `ghcr.io/apythoneer` (`:<sha>`), repo + packages private.

| Stage | Runner | Target | Overlay | Contents |
|---|---|---|---|---|
| CI (build/test/push) | GitHub-hosted | `ghcr.io/apythoneer` | — | backend, frontend, keepass images |
| Deploy **AWS dev** | **EC2** (`ec2-dev`) | EKS `c-lnk78` / `…-na-dev` | `overlays/dev` | full app |
| Deploy **AWS prod** | **EC2** (`ec2-prod`) | EKS `<prod>` / `…-na` | `overlays/prod` *(new)* | full app |
| Deploy **Azure collector** | **Azure VM** (`azure-prod`) | AKS `c-jmkcx` / `…-na` | `overlays/azure-collector` *(re-scoped from today's `overlays/prod`)* | collector only |

**Promotion:** merge to `main` → CI builds → dev auto-deploys (EC2). Advancing the `prod` branch → prod (EC2, AWS EKS) **and** the Azure collector (Azure VM, AKS) both deploy, each pinned to the promoted SHA.

---

## 5. Overlay plan (Kustomize)

| Overlay | Cluster | Namespace | Workloads | Env-specific needs |
|---|---|---|---|---|
| `overlays/dev` *(exists)* | EKS c-lnk78 | `…-na-dev` | backend + collector + frontend | EKS **ADOT OTel opt-out** annotations |
| `overlays/prod` *(NEW — build)* | EKS prod | `…-na` | backend + collector + frontend | EKS ADOT OTel opt-out; prod replicas/limits |
| `overlays/azure-collector` *(re-scope #68)* | AKS c-jmkcx | `…-na` | **collector only** + keepass-broker | **Azure Policy hardening** (non-root, ro-rootfs, drop caps, no SA-token); nginx/frontend/backend removed |

> Today's `overlays/prod` (from #68) was built for AKS with the **full** stack. It must be **split**: keep the hardening + keepass as `overlays/azure-collector` (collector only), and create a new `overlays/prod` for **AWS EKS** carrying the full app.

---

## 6. Connectivity matrix (proven this session)

| From ↓ / To → | on-prem 1433 | AWS 10.168.x | GCP arrays | Azure arrays | AWS Postgres 5432 |
|---|---|---|---|---|---|
| **AWS EKS pod** | ✅ | ✅ | ✅ | ❌ (firewall) | ✅ |
| **AKS pod (Azure)** | ✅ | ✅ (app-layer, verified) | — | ✅ | ✅ (protocol-verified) |
| Azure **VM** | ✅ | ❌ | — | ✅ | ❌ |
| Laptop | — | — | — | — | — (corp TLS inspection blocks the API) |

Consequence already banked: **no cross-CSP relay needed** — the AKS collector writes straight to AWS Postgres.

---

## 7. Deployment issues — identified

| Namespace | Issue | Status |
|---|---|---|
| **AWS dev** (EKS c-lnk78 / `…-na-dev`) | EKS **CloudWatch ADOT** webhook injected a broken OTel Python agent → `ImportError: typing_extensions` crashloop | **FIXED** via opt-out annotations (#66) |
| **AWS prod** (EKS) | **Never deployed** — no prod EKS kubeconfig obtained; no `overlays/prod`-for-EKS or EC2 prod runner yet | **OPEN** — primary gap |
| **Azure** (AKS c-jmkcx / `…-na`) | **Azure Policy / Gatekeeper** rejects non-hardened pods; #68 deployed the **full** stack here (over-scoped) | Hardened (#68) but **needs re-scoping to collector-only** |

---

## 8. Action items

1. **Confirm AWS prod specifics** — the prod EKS cluster id + its Rancher URL, and whether the EC2 reaches it (as it does the nonprod Rancher). _(Input needed.)_
2. **Create `overlays/prod` for AWS EKS** — full app, EKS ADOT opt-out, prod sizing. Add an **`ec2-prod`** runner label (or reuse `ec2-dev`) + a deploy job targeting AWS prod.
3. **Re-scope #68** — rename today's AKS `overlays/prod` → `overlays/azure-collector`; strip frontend + backend + the API service; keep collector + keepass-broker + the Azure Policy hardening. Point its `KEEPASS_URL`/DB at the central AWS Postgres.
4. **Register runners** — `ec2-prod` on the EC2; `azure-prod` on the Azure VM (started under #68's plan).
5. **DB target** — collectors (AWS + Azure) write to AWS Postgres `stormart01p`; port the app off SQL Server (driver → psycopg, `sslmode=require`). Until then, dev/prod may stay on on-prem SQL Server.
6. **GCP** — confirm the GCP VM's role; confirm SCPSB-1065 ("GCP Rancher Namespace", Done) is not a live collection namespace (GCP is collected from AWS). Reconcile or close.

---

## 9. Inputs needed from you

- AWS **prod EKS** cluster id + Rancher URL (I only had nonprod `c-lnk78` this session).
- GCP **VM** name + intended role.
- Confirm the EC2 should drive **both** AWS dev and prod (one runner, two labels) vs. a separate prod runner.
- Confirm the AKS namespace runs **collector only** (frontend/backend removed).
