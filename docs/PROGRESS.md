# Progress & Pending Tasks

_Last updated: 2026-09-17._

A running record of what's shipped and what's queued. For architecture see
[ARCHITECTURE.md](ARCHITECTURE.md); for the changelog see the README Version History.

## Recently shipped (deployed to `usodclpsandadm1`)

| Area | What | Status |
|------|------|--------|
| **Reliability** | Collector/API container split; autoheal; multi-worker API; DB-aware `/health/ready`; per-request statement timeouts; pyodbc pre-ping; migrations gated to the collector | ✅ live |
| **Alerting** | Alert collection across all 7 vendors; interval raised to **1 min**; per-array severity overrides | ✅ live |
| **Datadog paging** | Event-Management v2 intake; runtime on/off switch (sends from enable-point only, restart-safe); captures event id + link; scopable by group + vendor (e.g. Azure Pure only). Teams stays on regardless | ✅ live |
| **Metric expansion** | NetApp controller load (CPU%); NIC utilization (Pure + NetApp); Pure SAN/queue latency, over-subscription (`total_provisioned`), port errors, HW temp — via Pure v2.17 client | ✅ live |
| **Analytics rebuild** | Server-side time-bucketed `/analytics/fleet-trend`; interactive metric selector, streamlined filters; fixed the disconnected-lines + truncation bugs | ✅ live |
| **External partner API** | Isolated `/api/ext/v1` (hosts + volumes, read-only); per-client SHA-256-hashed keys, scopes, rate limit; key generator + partner docs | ✅ live |
| **Fleet Overview dashboard** | New `/fleet` page: cross-filtering slicers (cloud/DC/vendor/tech), capacity treemap, breakdown bars, sortable table; smart-disable of impossible filter combos; treemap responsive + tank-fill; `no-store` cache fix | ✅ live |
| **SIP rebrand** | UI rebranded SIP (backend stays USM) | ✅ live |
| **Repo cleanup** | Removed one-off dev/patch scripts; archived historical plans; refreshed README + `.env.example`; added this doc; local + remote stale files cleared | ✅ this change |

## Pending / in flight

| Priority | Task | Owner | Notes |
|----------|------|-------|-------|
| High | **Vendor metrics fast-follow** — controller load + IOPS/latency for HPE, Hitachi, Dell, Oracle; NetApp over-subscription | platform | Fills the empty series in the Analytics metric selector; per-vendor REST probing |
| High | **Per-CSP distributed collector fleet** — thin collector per cloud relaying to the on-prem API over HTTPS (443); centralized DB, minimal footprint | platform | Recon **blocked** on enrolling the SSH key on the AWS box (`awuse2lsanadmp01`); relay-via-API pattern chosen. Recon script: `reports/csp_recon.sh` |
| Med (external) | **Pure 400s** — regenerate API tokens for `eus2-02`, `eus2-03`, `cus-01` in KeePass | array owner | Tokens stale/revoked; not fixable platform-side |
| Med | **Partner API hardening** — TLS front door before real partner traffic (`:8080` is plain HTTP; key travels in the clear) | network | Key already provisioned for `Thad.Hinz@lumen.com` |
| Low (backlog) | **Datadog event details + ServiceNow ticket linkage** | platform | Needs a Datadog **application** key (API key alone 401s) |

## Known constraints
- Pure controller **CPU** is unavailable via REST (v1.19 + v2.17, CBS + physical);
  `queue_depth` / queue-latency stand as the load proxy. Pure1 SaaS API (App ID +
  private key) is the only path to true Pure CPU.
- Each CSP's private `10.x` space is only routable from **inside** that CSP —
  hence the per-CSP collector design.
