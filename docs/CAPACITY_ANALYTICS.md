# Capacity Analytics & Growth

Feature added in the v3.1 line: fleet capacity broken down by **platform (vendor)** and
**cloud (group)**, plus **per-array YTD growth** trends.

## What was added

### Backend — `backend/app/api/v1/analytics.py`

Two new endpoints (both read-only, run in a threadpool so they don't block the event loop):

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/analytics/capacity-breakdown` | Usable / used / free / allocated capacity grouped by vendor, by cloud, and as a vendor×cloud pivot. |
| `GET /api/v1/analytics/array-growth/{array_name}?months=12` | Per-array YTD growth + trailing-N-month monthly capacity trend. |

**Capacity semantics** (all sizes returned in TB / TiB = 1 099 511 627 776 bytes):

- **Usable** = `SUM(capacity_total)` — provisioned/available capacity
- **Allocated** = same as usable (the raw provisioned figure)
- **Used** = `SUM(capacity_used)`
- **Free** = `Usable − Used`
- **Utilization %** = `Used / Usable × 100`

**Data sources:**
- `metrics_current` — live per-array capacity (joined on `array_name`)
- `managed_arrays` — vendor + `group_label` (the "cloud" dimension, e.g. *Azure CBS*, *AWS CBS*, *On-Prem*)
- `metrics_history` — time-series used for growth trends

The breakdown uses a **LEFT JOIN** so an array present in `metrics_current` but missing
from `managed_arrays` still appears, attributed to cloud `Unassigned`.

### Backend — history retention

`metrics_history` was previously trimmed to **7 days**, which made YTD growth impossible.

- `backend/app/core/config.py` — new setting `history_retention_days` (env `HISTORY_RETENTION_DAYS`, default **365**).
- `backend/app/services/stats.py` — `cleanup_old_history(days=None)` now defaults to that setting.
- The scheduler's daily `history_cleanup` job (`01:00 UTC`) automatically honours the new default.

> ⚠️ YTD/growth numbers only become meaningful once history has accumulated. A freshly
> deployed system shows growth from the first retained sample, not from a true Jan 1
> unless data existed then. To backfill, import historical capacity rows into
> `USM.metrics_history`.

### Frontend

| File | Change |
|---|---|
| `frontend/src/api/types.ts` | Added `CapacityBreakdown`, `CapacityBucket`, `VendorBucket`, `CloudBucket`, `VendorCloudBucket`, `ArrayGrowth`, `GrowthTrendPoint`. |
| `frontend/src/api/arrays.ts` | Added `capacityBreakdown()` and `arrayGrowth(name, months)` client methods. |
| `frontend/src/pages/Capacity.tsx` | New page: fleet summary cards, Used-vs-Free stacked bar (toggle Platform/Cloud), By-Platform & By-Cloud tables, and a Platform×Cloud pivot table. |
| `frontend/src/App.tsx` | Registered route `/capacity`. |
| `frontend/src/components/layout/Sidebar.tsx` | Added "Capacity" nav item (PieChart icon). |

## How to verify

```bash
# Backend syntax
python -m py_compile backend/app/api/v1/analytics.py backend/app/core/config.py backend/app/services/stats.py

# Live (once backend is running)
curl http://<host>:8000/api/v1/analytics/capacity-breakdown
curl "http://<host>:8000/api/v1/analytics/array-growth/<ARRAY_NAME>?months=12"
```

Frontend: `cd frontend && npm install && npm run dev`, then open **Capacity** in the sidebar.

## Example response shapes

`capacity-breakdown`:
```json
{
  "fleet": {"arrays": 42, "usable_tb": 1280.5, "used_tb": 760.2, "free_tb": 520.3, "allocated_tb": 1280.5, "utilization_pct": 59.4},
  "by_vendor": [{"vendor": "pure", "arrays": 20, "usable_tb": 800.0, "used_tb": 500.0, "free_tb": 300.0, "allocated_tb": 800.0, "utilization_pct": 62.5}],
  "by_cloud":  [{"cloud": "Azure CBS", "arrays": 15, "usable_tb": 600.0, "used_tb": 380.0, "free_tb": 220.0, "allocated_tb": 600.0, "utilization_pct": 63.3}],
  "by_vendor_cloud": [{"vendor": "pure", "cloud": "Azure CBS", "arrays": 10, "usable_tb": 400.0, "used_tb": 250.0, "free_tb": 150.0, "allocated_tb": 400.0, "utilization_pct": 62.5}]
}
```

## Excel export (per-array + grouped)

A multi-sheet `.xlsx` report can be downloaded on demand.

**Endpoint:** `GET /api/v1/analytics/capacity-export.xlsx` (in `analytics.py`)
Requires `openpyxl` (added to `backend/requirements.txt`). Returns a streamed
`.xlsx` attachment.

**UI:** the **Capacity** page has an **Export Excel** button (top-right) wired to
`arraysApi.exportCapacityXlsx()` in `frontend/src/api/arrays.ts`, which downloads
the blob and triggers a browser save.

**Workbook sheets:**
| Sheet | Contents |
|---|---|
| Arrays | One row per array: name, vendor, cloud type, CSP, group, model, status, usable/used/free TB, util %, data reduction, collected-at. |
| By Cloud Type | Totals split into **On-Prem** vs **Cloud**. |
| By CSP | Totals per cloud provider (Azure / AWS / GCP / OCI / On-Prem). |
| By Vendor | Totals per platform (pure, netapp, hpe, dell, hitachi, oracle). |
| By Group | Totals per raw `group_label`. |
| Vendor x CSP | Vendor × CSP pivot. |

**Cloud classification** (`_classify_cloud` in analytics.py): keyword match over
`group_label` + `site` + `technology`. Cloud groups look like `Cloud-AZU-EUS2`,
`Cloud-AWS-EUS2-2a`, `azure`; on-prem groups are 3-letter datacenter codes
(`DDC`, `ODC`, `IDC`, `ADC`, `MDC`, `CDC`).

### Standalone generator (no redeploy needed)

`scripts/generate_capacity_report.py` builds the same workbook by calling the
live `/api/v1/arrays` endpoint — useful before the backend is redeployed with
openpyxl, or for ad-hoc reports from a workstation:

```bash
pip install requests openpyxl
python scripts/generate_capacity_report.py --base-url http://<host>:8000 --out usm_capacity_report.xlsx
```

> Latest run (85 arrays): On-Prem = 62 arrays / 31.2 PB usable; Cloud = 23 arrays /
> 942 TB (Azure 15, AWS 8).

`array-growth/{name}`:

```json
{
  "array_name": "FA-PROD-01",
  "months": 12,
  "current": {"usable_tb": 40.0, "used_tb": 28.0, "used_pct": 70.0, "collected_at": "2026-06-10T12:00:00"},
  "ytd": {"start_date": "2026-01-01T00:05:00", "start_used_tb": 22.0, "current_used_tb": 28.0, "growth_tb": 6.0, "growth_pct": 27.3},
  "trend": [{"date": "2026-01-01T00:05:00", "usable_tb": 40.0, "used_tb": 22.0, "used_pct": 55.0}]
}
```
