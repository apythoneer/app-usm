# USM Performance & Architecture Review — June 2026

Live assessment of the deployed v3.0.0 stack on `usodclpsandadm1.corp.intranet`,
based on `/health` and `/scheduler/status` plus a code review of the collector
write path and DB layer.

---

## Live snapshot

| Signal | Value |
|---|---|
| Backend | healthy, v3.0.0, DB reachable |
| Cache contents | 87 arrays, **45,683 volumes**, 3,192 hosts |
| Metrics jobs | fast (1–12s per vendor), every 5 min |
| Volume jobs | **SLOW: 420–470s each**, every 30 min |
| NetApp auth | **11 of 23 arrays failing "Authentication failed"** (metrics, volumes, alerts) |
| HPE volumes | 2 arrays failing with **ODBC `Query timeout expired`** during `save()` |

---

## Implementation status

**P1 fix SHIPPED (2026-06-19).** The volume/host save path was rebuilt to be
set-based and batched. Summary of changes:

- `backend/app/db/session.py`
  - new `get_fast_cursor()` — yields a cursor with `fast_executemany=True`.
  - new `batch_upsert()` — one SELECT to load existing keys, in-memory
    insert/update partition, then chunked `executemany()` INSERT/UPDATE and a
    chunked batch DELETE for stale rows. Supports a per-vendor `extra_where`
    (used by NetApp's `vendor='netapp'` filter).
- `pure/volumes.py`, `netapp/volumes.py` — now call `batch_upsert` for
  volumes/hosts/host_groups/protection_groups (upsert semantics preserved).
- `hpe/volumes.py`, `dell/volumes.py`, `hitachi/volumes.py`, `oracle/volumes.py`
  — delete-then-insert path converted from per-row `cursor.execute` loops to a
  single `cursor.executemany()` on a fast cursor. Hitachi keeps its
  case-insensitive host de-dup (done while building the param list).
- `backend/app/collectors/base.py` `_write_to_cache()` — SQLite dual-write
  converted from per-row `INSERT OR REPLACE` loops to `executemany()`.

Validation: `py_compile` clean on all 8 files; `validate_collector(pure)` and
`lint_schema` both pass. **Pending:** observe next scheduled volume cycle in
`/scheduler/status` to confirm job duration drops from ~450s.

---

## Findings (ranked by impact)

### 🔴 P1 — Volume `save()` is an N+1 round-trip storm  ✅ FIXED

**File:** `backend/app/collectors/*/volumes.py` (`_save_volumes`, `_save_hosts`,
`_save_host_groups`, `_save_protection_groups`)

For each of ~45.7k volumes (plus hosts/hgroups/pgroups) the code does:
1. `SELECT id ... WHERE array_name=? AND volume_name=?` (existence check)
2. then a single-row `UPDATE` **or** `INSERT`

That's **~90,000+ serial DB round-trips per cycle**, per vendor, single-threaded.
This is the direct cause of the 7–8 minute job durations and the intermittent
`Query timeout expired` failures (the 60s ODBC query timeout trips under load).

**Fix options (biggest win):**
- Use `cursor.fast_executemany = True` + `executemany()` for batched INSERT/UPDATE
  (pyodbc + ODBC 17/18 supports this; 10–50× fewer round-trips).
- Or switch to a **set-based MERGE** into a TVP / temp table: bulk-insert all rows
  into a `#staging` table with one `executemany`, then a single `MERGE` /
  `DELETE`+`INSERT` against `volumes_cache`. This removes the per-row SELECT entirely.
- Pre-load existing keys with **one** `SELECT volume_name FROM volumes_cache WHERE array_name=?`
  (already done for deletes) and reuse that set to decide insert-vs-update in memory,
  eliminating the per-row `SELECT id`.

**Expected result:** volume jobs drop from ~450s to a few seconds; timeouts disappear.

### 🔴 P1 — SQLite cache write also loops per-row
**File:** `backend/app/collectors/base.py` `_write_to_cache()`

The dual-write to SQLite also does per-row `INSERT OR REPLACE` in a Python loop.
Wrap in a single transaction and use `executemany()`. SQLite is local so the
impact is smaller than SQL Server, but at 45k rows it still adds up.

### 🟠 P2 — 11 NetApp arrays failing authentication
**Files:** `backend/app/collectors/netapp/client.py`, inventory / cred mapping

The failing arrays look like the bare `naXX1an01` nodes (capacity 0, status
`degraded`). Either they have no `cred_key` mapped in `managed_arrays`, stale
KeePass entries, or they are decommissioned and should be disabled. Every failed
auth still costs a collection attempt × 3 job types × every interval.

**Fix:** audit `managed_arrays.cred_key` for NetApp; disable dead nodes
(`enabled=0`) so the scheduler skips them; alert when an array flips to failing.

### 🟠 P2 — No visibility into per-array timing / failures
The scheduler tracks aggregate success/fail but there's no persisted history of
job durations or a UI surface for "which arrays are slow / failing". Operators
only see this via the raw `/scheduler/status` JSON.

**Fix:** persist collector run stats (duration, success, error) to a small table
and add a "Collectors / Jobs health" panel to the dashboard.

### 🟡 P3 — Volume collection concurrency
`ThreadPoolExecutor(max_workers=5)` serializes 24 Pure arrays' volume pulls. Once
P1 removes the DB bottleneck, the API-fetch time dominates. Consider a separate,
slightly larger pool for the (network-bound) volume collectors, or stagger less.

### 🟡 P3 — `metrics_history` growth & query cost
Retention is now 365 days (good for the new capacity-growth feature) but
`metrics_history` will grow large. Ensure the `IX_history_array` /
`IX_history_time` indexes cover the analytics queries; consider a covering index
on `(array_name, collected_at)` and periodic index maintenance.

### 🟡 P3 — API read path consistency
Some endpoints read SQLite cache, some hit SQL Server. Document/standardize which
path each endpoint uses so heavy dashboard polling never falls back to SQL Server
under load.

---

## Recommended sprint order

1. **P1 — Batch the volume/host/pgroup save path** (SQL Server + SQLite). Highest
   ROI; fixes both slowness and timeouts. Start with Pure (largest), then roll the
   same pattern to the other 5 vendor volume collectors.
2. **P2 — NetApp credential/inventory cleanup** + disable dead nodes.
3. **P2 — Collector run-stats table + dashboard health panel.**
4. **P3 — Concurrency tuning, history index review, read-path documentation.**

---

## Notes / open questions for the team
- Are the failing `naXX1an01` NetApp nodes decommissioned, or should creds be fixed?
- Is there an appetite for a TVP/MERGE approach (needs a SQL Server table type), or
  prefer the lower-risk `fast_executemany` batching first?
- Target volume refresh interval — is 30 min acceptable, or do you want it faster
  once the save path is optimized?
