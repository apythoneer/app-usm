# USM Platform — Audit & Remediation Roadmap

_Generated 2026-07-22 from a 17-agent reverse-engineering audit: 15 areas (every frontend page + backend subsystem), 183 findings with file:line evidence, then synthesis and architecture stages._

## Executive summary

Across ~183 audited findings, the USM (Unified Storage Monitoring) platform has three classes of problems that dominate: (1) a complete absence of authentication on a host-networked API whose mutating endpoints rewrite webhooks, trigger credential lookups, and execute LLM-generated SQL; (2) multiple defects that silently corrupt or misrepresent the exact numbers the product exists to report — capacity, growth projections, and alert state; and (3) a hard single-worker deployment ceiling amplified by pervasive N+1/connection-per-row patterns, unbounded/capped queries, and shared-pool head-of-line blocking that all worsen linearly at the planned 2-5x fleet growth (~97 → ~485 arrays). Beneath these sit strong systemic patterns: silent truncation via capped/unpaginated queries, alert lifecycles that never close (so USM.messages grows unbounded and cleared alerts show as active), partial collector failures that overwrite good data with zeros, timestamps stored as strings (breaking sargability and TRY_CAST), unit confusion (TiB labeled as TB, base-1024 vs base-1000), vendor-identity drift (storagegrid persisted as netapp; enum omissions), read-only/notification guarantees enforced by prompt/convention rather than code, and heavy copy-paste duplication that has already drifted. After merging duplicates, roughly 15 finding-pairs collapse (e.g. the two chat off-by-index reports, the two host-storage-report N+1 reports, the two single-array-history TOP-ASC reports, three TB/TiB reports, two vendor-enum-drift reports, two SortHeader-duplication reports). The most severe, highest-confidence, highest-impact issues cluster in security, capacity-data integrity, and alert lifecycle.

**Totals:** 183 findings — 1 critical · 22 high · 77 medium · 83 low. By kind: 43 inconsistency, 40 bug, 37 risk, 21 inefficiency, 20 tech-debt, 14 ux, 8 broken. 145 are confirmed (exact broken line cited).

## Themes (severity-ranked)

| Sev | Theme | # | Why it matters |
|---|---|---|---|
| CRITICAL | Missing authentication & security exposure | 8 | A host-networked (network_mode: host, 0.0.0.0:8000) API with Swagger enabled and ZERO auth on any route lets anyone with network reach enumerate the full multi-vendor fleet, add/delete/disable arrays, rewrite the Teams webhook (redirect/suppress all alerting), trigger credential lookups + outbound array logins, and run LLM-generated SQL. A placeholder SECRET_KEY (two different literals across config/compose) means Phase-3 JWTs would be trivially forgeable, and the DGX chat path silently egresses inventory/capacity rows off-network, contradicting the 'no data leaves' promise. |
| HIGH | Capacity data corruption & projection integrity | 8 | Capacity/growth reporting is the product's core value, yet several confirmed defects poison it: metrics collectors seed a truthy dict so the 'no data' guard never trips and a single transient GET writes capacity_used_pct=0 into metrics_current AND a 0 row into metrics_history (artificial cliffs in projections); analytics/stats aggregations omit the disabled-array filter that fleet-stats applies, so Dashboard and Capacity pages report different fleet TB; single-array history uses TOP...ORDER BY ASC (returns oldest rows, drops 'now'); the fleet-history 5000-row cap silently truncates the window at fleet scale; utilization is unweighted in one place and weighted in another; and linear projection fits row-index not calendar-date so gaps inflate days-to-full. |
| HIGH | Alert lifecycle broken & unbounded messages growth | 7 | For a monitoring tool, alerts that never clear are a core failure. Pure fetches only open messages with no absence-based resolve; NetApp auto-resolve and the nightly purge both use TRY_CAST(opened AS DATETIME2) on timezone-bearing strings which returns NULL (predicate always false, both inert); Hitachi/Dell/Oracle/HPE collectors never mark cleared alerts resolved. Because cleanup only deletes resolved=1, USM.messages grows without bound and long-cleared criticals display as active. Capacity Teams notifications are also suppressed on re-open (teams_notified not reset), the TEAMS_SEVERITIES filter is dead code, and Teams POSTs have no retry so transient outages drop alerts for up to a week. |
| HIGH | Alert identity / dedup instability | 5 | Dell/Oracle/Hitachi derive message_id from Python hash() of the native id, which is per-process salted (no PYTHONHASHSEED pinned), so every container restart re-inserts and re-notifies all open alerts — an alert storm on each deploy. Empty native ids collapse to message_id=0 (unrelated alerts merged), NetApp's INT message_id can overflow 2^31 on long-lived clusters, one id-less/duplicate row rolls back the whole per-array batch, and fractional capacity thresholds collide on the same synthetic id. |
| HIGH | Per-vendor collector inconsistency | 12 | The 7 vendor collectors diverge in ways that cause silent data loss and unreachable arrays. Only NetApp/StorageGrid have the fqdn→mgmt_ip→name host-fallback; HPE/Hitachi/Dell/Oracle bind one host at construction (arrays reachable only by mgmt_ip stay permanently uncollected) and Pure ignores cred_key. StorageGrid metrics are written as vendor='netapp'. Hitachi (count=8192, no pagination), Dell (per_page caps), and NetApp (get_all 5000) silently truncate large inventories; because truncated counts are stable, the shrink guard never fires. Oracle's online-only pool filter can yield 0 volumes and never collects hosts. capacity_total means different things per vendor under one column. |
| HIGH | Single-worker scale ceiling & DB connection storms | 13 | The biggest scale gap: the code supports an api-only/collector split (RUN_SCHEDULER) but the shipped compose runs one uvicorn worker sharing the event loop across API, scheduler, and chat. This is amplified by N+1/connection-per-row patterns (storage-report one query per server, inventory upsert per array, capacity_alerts opening a connection per array and holding it across a 10s Teams POST), slow serial vendor collectors (Hitachi ~370s, Oracle deep N+1) that hold one of only 16 shared threads and starve every other vendor, a global 60s refetchInterval plus unfiltered Navbar invalidateQueries fan-out (thundering herd), an unlocked arrays cache that stampedes on expiry, and a KeePass refresh loop that becomes a tight retry storm when KeePass is down. All worsen linearly at 2-5x. |
| HIGH | Chat / Text-to-SQL safety & correctness | 13 | The read-only guarantee is breakable and the pipeline is fragile. SELECT...INTO passes the validator (a write in a supposedly read-only path); the table allowlist skips unqualified and comma-joined tables; add_safety_limits produces invalid T-SQL for DISTINCT (breaking the whole CMS feature) and leaves outer CTE/UNION results unbounded; CHAT_QUERY_TIMEOUT is defined but never applied so LLM SQL can run to the 60s connection timeout; per-call 180s timeouts can stack to ~540s while the client already gave up at 120s, pinning 1 of 2 chat slots on dead work. Correctness also suffers: multi-turn context pairs questions with the wrong SQL, forecast intent misroutes ordinary questions, the date parser treats 'may' as a date, and SQL extraction leaks reasoning on orphan think-tags. |
| HIGH | DB schema/migration fragility & query hygiene | 12 | init_database runs all DDL inline on every boot with no migration framework; the volumes_cache/hosts_cache vendor ALTERs run BEFORE those tables are created, so any fresh/DR/staging DB crash-loops on startup (taking all collectors down). An unconditional ALTER on metrics_current runs every boot. Timestamps stored as NVARCHAR break sargability and TRY_CAST. There's no composite (array_name, collected_at) index for the window-function analytics, NOLOCK is applied inconsistently on collector-mutated cache tables (dirty/partial reads), COUNT and page run in separate connections (pagination total mismatch), and limit params have no lower bound (negative TOP → 500). |
| HIGH | Inventory sync robustness | 6 | The nightly DimStorageFinance sync aborts entirely on a single row with NULL Vendor or NULL ArrayNameKey (.get(col,default) returns None on SQL NULL → AttributeError outside the per-row try), leaving inventory half-synced. It is per-array N+1, resolve_cred_key has a case-sensitive StorageGrid match (real branding 'StorageGRID' misses) and hands unknown Hitachi models a hardcoded F900 credential, decommissioned arrays get two different status strings, duplicate ArrayNameKeys are last-wins, and the whole thing depends on a load-bearing 'Dispostition' column typo. |
| MEDIUM | Frontend-backend contract drift | 9 | Contracts are enforced by convention, not types, and have already drifted. The frontend Vendor union omits active vendors (storagegrid/ibm/veritas) and lists inactive ones; the 'official' PaginatedResponse schema is dead and contradicts the real {total,limit,offset,data} wire shape (any new endpoint adopting it renders empty); collection-interval controls only retune Pure jobs despite generic labels; Add-Array emits a cloud label taxonomy the badge/grouping logic doesn't recognize; row mappers default missing vendor to 'pure' while schemas default 'unknown'; several API client methods return any. |
| MEDIUM | Missing/inconsistent error handling & silent failures | 11 | The most dangerous failure mode for a monitoring dashboard: outages rendered as healthy. fleet-stats/alerts queries have no isError handling so a backend outage shows 'all clear' / green. Frontend mutations are onSuccess-only (notes save, array toggle/delete, edit-array, add-verify) so failures are silent and can lose edits (NotesCell Escape actually SAVES and Enter double-writes). Clipboard copy reports success even when it throws. Backend has pervasive bare except/pass (a DB outage silently degrades the dashboard to arrays.txt). A single root ErrorBoundary white-screens the whole shell. |
| MEDIUM | Analytics/dashboard correctness & display | 11 | Charts and headline stats actively mislead. Severity sorts alphabetically (buries criticals when triaging); Top-N is ranked globally then intersected with the vendor filter (vendor+TopN can render an empty 'no data' chart); Top-N ranks by summed IOPS (biases toward denser-sampled arrays); latency charts inherit the IOPS Top-N (worst-latency arrays hidden); connectNulls draws straight lines across outages (concealing exactly what operators need to see); the active-alerts severity sub-label is computed from only the 50 newest rows; only 20 colors exist for up to 97 arrays; and daily-trend date labels shift a day earlier in US timezones. |
| MEDIUM | Unit/measurement & formatter inconsistency | 6 | The numbers are the product, yet capacity is divided by 2^40 (TiB) but labeled 'TB' everywhere (~10% understatement vs vendor GUIs), and formatBytes (base-1024) disagrees with formatTB (base-1000) so the same fleet shows different totals in the StatCard vs the per-array table at PB scale. formatBytes renders null as '0 B' (looks like an empty array) where every other formatter returns '—', and data_reduction defaults to 1.0 in some endpoints and 0.0 in the Excel export. |
| MEDIUM | Frontend rendering, bundle & modal UX at scale | 9 | Client-side scale and polish gaps: no route code-splitting (single ~800KB bundle ships recharts to every visitor), no virtualization (hundreds of array rows re-rendered every 60s, plus a second full copy in the modal), tables/charts blank to 'Loading…' on every page/sort/filter change (no placeholderData), Tailwind brand-400/300 shades are undefined so the primary active-state color for pagination and the sidebar silently renders as a no-op, and modals lack Escape/focus-trap/scroll-lock. Drilldown lists hard-cap at 200 and the volume picker at 500 with no truncation indicator. |
| LOW | Duplication / DRY & maintainability | 7 | Heavy copy-paste means fixes must be made in many places and drift is already visible. SortHeader is duplicated across four pages (Dashboard's copy reordered props); the filter/sort/color pipeline is duplicated Dashboard vs modal (status filter present in one, absent in the other); the fetch/paginate/sort/JSON block and _safe_json are copy-pasted across three routers; _TIB, the pagination envelope, the StorageGrid remap (with different casing rules), and the linear-regression slope are all reimplemented; Analytics has three near-identical pivot useMemos with linear scans in the hot loop. |

## Top priorities (do these first)

1. **[CRITICAL] No authentication on any endpoint (incl. mutating), on a host-networked API with Swagger enabled** — Anyone with network reach can enumerate the full fleet, add/delete/disable arrays, rewrite the Teams webhook (suppress/redirect all alerting), trigger credential lookups + outbound array logins, and execute LLM-generated SQL — no credential required, with self-documenting /docs.
   - *Fix:* Gate all non-health routes behind a global auth dependency before any production exposure; wire the auth/login router; disable /docs & /redoc in prod; fail startup if SECRET_KEY is still the placeholder; do not rely on network_mode: host isolation.
2. **[HIGH] Metrics collectors overwrite good capacity with 0 on any partial API failure, poisoning metrics_current and projection history** — A single transient GET writes capacity_used_pct=0 to metrics_current and appends a 0 row to metrics_history, creating artificial cliffs that corrupt growth projections and flash 0% on the dashboard; StorageGrid is worst-hit via Prometheus query timeouts.
   - *Fix:* Skip/keep-existing when key metric sections are missing (mirror the volumes empty-collect guard); never INSERT a metrics_history row when capacity_total is unknown; use COALESCE-to-existing instead of default-0 in the metrics_current UPDATE.
3. **[HIGH] Alert auto-resolve and purge use TRY_CAST(opened AS DATETIME2) on timezone-bearing timestamps → NULL → both inert** — NetApp auto-resolve and the nightly resolved-alert purge match nothing, so USM.messages grows unbounded and cleared criticals show as active. Compounds Pure/Hitachi/Dell/Oracle/HPE never marking cleared alerts resolved at all.
   - *Fix:* Cast via DATETIMEOFFSET (or store a real DATETIME2 column at insert); add absence-based auto-resolve to every vendor collector (mark rows resolved when their id drops out of the freshly collected set).
4. **[HIGH] Alert dedup uses per-process-salted hash() for message_id → alert storm on every restart** — With no PYTHONHASHSEED pinned, the same source alert maps to a different message_id after each container restart, so every open alert re-inserts and re-fires Teams on each deploy; empty ids also collapse to message_id=0, merging unrelated alerts.
   - *Fix:* Derive a stable id from a hash of the vendor-native key (e.g. int(hashlib.md5(key).hexdigest()[:8],16)) or store the native string id; when native id is absent, hash full content rather than defaulting to 0; stopgap PYTHONHASHSEED=0.
5. **[HIGH] Fresh-DB startup crash: volumes_cache/hosts_cache vendor ALTERs run before the tables are created** — Any first install, DR rebuild, or fresh staging DB crash-loops on startup (ALTER on a non-existent table, unguarded, aborts the transaction), taking every collector down. Latent only because existing prod DBs already have the tables.
   - *Fix:* Move the vendor-column migrations to after their CREATE TABLE, or guard each with OBJECT_ID IS NOT NULL / try-except like the BIGINT migration; adopt a real migration tool or schema_version table.
6. **[HIGH] Analytics/stats aggregations omit the disabled-array filter, so Dashboard and Capacity report different fleet TB** — A disabled/decommissioned array lingering in metrics_current is counted in capacity-breakdown, top-growers, daily_stats trend, and the Excel report but excluded from fleet-stats/table — the exact drift the fleet-stats rewrite claimed to eliminate.
   - *Fix:* Apply the same enabled=1 exclusion (ideally INNER JOIN managed_arrays ma ON ... AND ma.enabled=1) consistently across all analytics/stats capacity aggregations via one shared helper.
7. **[HIGH] Single uvicorn worker couples API + scheduler + chat; the documented api/collector split is not deployed** — Hard single-worker ceiling: a collection storm or a couple of slow chat calls starves the API and flips the container unhealthy. The RUN_SCHEDULER split exists in code but the shipped compose runs one process for everything; worsens at 2-5x arrays.
   - *Fix:* Split compose into a collector container (RUN_SCHEDULER=true, 1 replica) and an API container (RUN_SCHEDULER=false, multiple uvicorn workers) behind the existing nginx — the code already supports it.
8. **[HIGH] /hosts/storage-report is an N+1 loop AND prefix-LIKE over-matches, double-counting shared volumes** — Despite a 'single JOIN' docstring it runs one query per server (serializing the request and holding a connection), and UPPER(host_name) LIKE ?+'%' matches sibling hosts and double-counts volumes shared by clustered hosts — inflating the provisioned/used bytes that feed capacity planning.
   - *Fix:* Batch OR'd predicates (or a TVP/temp-table JOIN) into one query per chunk; dedup (array,volume) keys into a set before aggregating; use exact/escaped matching; bound the servers list.
9. **[HIGH] SELECT ... INTO bypasses the SQL safety validator (read-only guarantee is breakable)** — 'SELECT * INTO USM.evil FROM ...' starts with SELECT, contains no blocked keyword, and the INTO target is never allowlist-checked — so a crafted/hallucinated query can create and populate tables in a pipeline whose entire premise is read-only.
   - *Fix:* Block T-SQL SELECT-INTO (reject a bare \bINTO\b that isn't part of the already-blocked INSERT INTO); prefer a real T-SQL parser (sqlglot) over regex; also enforce the allowlist for unqualified and comma-joined tables.
10. **[HIGH] add_safety_limits emits invalid T-SQL for DISTINCT (TOP before DISTINCT), breaking the whole CMS feature** — Every 'which apps/databases are on array X' query (all DISTINCT) becomes 'SELECT TOP 100 DISTINCT …' — a syntax error that wastes the most expensive LLM fix round-trip and can never succeed if the fix reproduces a bare DISTINCT. It also leaves outer CTE/UNION results unbounded.
   - *Fix:* Insert TOP after any leading ALL/DISTINCT group; apply the row cap to the final/top-level SELECT (or wrap the whole statement as SELECT TOP 100 * FROM (<sql>) _lim) so CTE/UNION outer sets are bounded.
11. **[HIGH] Single-array history returns the OLDEST rows (TOP … ORDER BY ASC), dropping 'now' on wide windows** — For 30-day windows the endpoint returns only the oldest ~1440 points and omits recent data with no error — the exact defect already fixed for fleet-history but left in the single-array path.
   - *Fix:* Mirror the fleet-history fix: SELECT TOP {limit} ordered by collected_at DESC in an inner query, then re-sort ASC in the outer query; add ge=1 lower bounds to the limit params.
12. **[HIGH] Hitachi/Oracle volume collection is deep serial N+1 (~370s) that starves the shared 16-thread pool** — One host-WWN call per host group (up to 500) plus 180s/90s serial timeouts pins one of only 16 shared workers for minutes, so a handful of slow arrays starve every other vendor's collectors; worsens linearly at 2-5x.
   - *Fix:* Bulk-fetch host-WWNs per port instead of per host group; parallelize/bound per-array enrichment; give slow vendors a dedicated executor and cap timeouts.
13. **[HIGH] Silent inventory truncation: NetApp get_all 5000, Hitachi unpaginated count=8192, Dell per_page caps** — Large arrays lose their volume tail every cycle; because the truncated count is stable, the would_shrink_below guard never fires, so missing volumes and understated capacity are permanent and invisible — degrading exactly at the fleet scale USM targets.
   - *Fix:* Implement real pagination in a get_all helper for each vendor (NetApp continuation, Hitachi nextPageHeadLdevId, Dell page/entryCount) and log/raise when a cap is hit.
14. **[HIGH] KeePass refresh loop becomes a tight retry storm when KeePass is unreachable** — On failure the entry's expiry isn't advanced, so every cached key stays 'due' and is retried each 60s wake; with 15s sequential timeouts across ~97 keys the loop falls permanently behind and hammers KeePass instead of backing off.
   - *Fix:* On refresh failure push the expiry forward (exponential backoff + jitter), cap attempts per wake, and bound total time per iteration; add a per-key single-flight guard.
15. **[HIGH] Inventory sync aborts entirely on a single row with NULL Vendor or NULL ArrayNameKey** — `.get(col,default)` returns None on SQL NULL, so None.strip()/None.lower() raise outside the per-row try and abort the whole nightly 03:00 sync, leaving inventory half-synced with earlier per-row commits already applied.
   - *Fix:* Coalesce NULLs before use ((row.get('ArrayNameKey') or '').strip()), compute vendor on its own line, skip rows with empty array_name, and wrap the full loop body in try/except.
16. **[HIGH] NotesCell blur handler saves on Escape and double-writes on Enter; every focus+blur PATCHes** — Escape saves the edit instead of cancelling (data-loss surprise), Enter fires two PATCHes, and merely focusing then clicking away issues a needless DB write + full ['volumes'] invalidation — redundant load at scale, and with no onError failures are silent.
   - *Fix:* Track a committed baseline and only mutate on real change; use a cancel flag so Escape suppresses the blur-save and Enter suppresses the following blur; add onError to surface/revert failures.
17. **[MEDIUM] HPE/Hitachi/Dell/Oracle clients have no host-fallback loop → arrays reachable only by mgmt_ip stay uncollected** — These four bind a single host at construction; any array with a wrong/duplicate/non-resolvable FQDN but a reachable mgmt_ip is permanently uncollected — the exact defect already fixed for NetApp/StorageGrid.
   - *Fix:* Port the NetApp candidate-host pattern: try a de-duped [fqdn, mgmt_ip, name] list in authenticate(), stop early on 401/403, and remember the working host.
18. **[MEDIUM] fleet-stats & active-alerts queries have no error handling — outage renders 'all clear'** — On a backend/fleet-stats/alerts failure the cards fall through to zero/green defaults and the Active Alerts card shows 'all clear' with a green icon — the most dangerous possible failure mode for a monitoring dashboard.
   - *Fix:* Surface isError for the fleet and alerts queries (inline error/badge) instead of falling through to zero/green; add a QueryCache onError global failure toast.
19. **[MEDIUM] Capacity Teams notification silently suppressed when an alert re-opens after resolving** — _upsert_alert re-opens the row (resolved=0) without resetting teams_notified, so a flapping array or a re-firing fleet projected-full alert sends NO Teams notification for up to CAPACITY_ALERT_RESEND_DAYS (7d) while opened is still incremented.
   - *Fix:* Clear teams_notified on the resolved=1→0 transition (or treat a freshly re-opened alert as new in _should_notify and always notify).
20. **[MEDIUM] Capacity labeled 'TB' is actually TiB (÷2^40), and formatBytes (1024) disagrees with formatTB (1000)** — All 'TB' figures are ~10% below decimal TB (breaks reconciliation against vendor GUIs and finance), and the same fleet shows different totals in the StatCard vs the per-array table at PB scale — undermining trust in a tool whose numbers are the product.
   - *Fix:* Pick one convention end-to-end (rename fields to *_tib, or divide by 1e12 for true TB) in one shared constant; align formatBytes and formatTB to the same base and correct the unit labels.
21. **[MEDIUM] Severity column sorts alphabetically, burying criticals; vendor+Top-N can render an empty chart** — Sorting by Severity desc lists warning→info→critical (criticals last, exactly when triaging), and Top-N ranked globally then intersected with the vendor filter can yield zero arrays, showing a misleading 'no data' state.
   - *Fix:* Sort by a severity rank (CASE critical=0/warning=1/info=2) on the backend; apply vendor/group filters first, then compute Top-N over the remaining arrays.
22. **[MEDIUM] connectNulls bridges data gaps on every analytics line, concealing array outages** — When an array stops reporting (collector failure/offline), recharts draws a straight line across the gap so an outage looks like smooth continuous data — actively hiding the condition operators most need to see on a monitoring tool.
   - *Fix:* Remove connectNulls (or make it opt-in) so gaps render as breaks; distinguish 'no data' from zero.

## Critical & High findings (detailed)

### [CRITICAL] No authentication on any endpoint, including mutating ones, exposed on host network
*risk · confirmed · ops/security*  
**Evidence:** backend/app/api/deps.py:13-18 defines get_current_user but the comment says 'Auth is wired but NOT enforced yet (no Depends(get_current_user) on routes)'. A grep for get_current_user/Depends(auth) across backend/app/api finds it wired into ZERO routes. router.py:7-18 does not even include an auth router, so the OAuth2 tokenUrl '/auth/login' (deps.py:15) 404s. Meanwhile mutating/side-effecting endpoints are fully open: POST/PUT/DELETE /arrays/managed (arrays.py:314-397), POST /arrays/managed/{name}/verify (arrays.py:400 — triggers live KeePass lookups + outbound array API auth), PATCH /volumes/notes (volumes.py:93), PUT /settings/notifications (settings.py:126), POST /settings/inventory-sync (settings.py:232), and POST /chat (chat.py:62 — executes LLM-generated SQL). docker-compose.yml:29 runs the backend with network_mode: host and Dockerfile:36 binds 0.0.0.0:8000, and /docs + /redoc are enabled (main.py:82-83).  
**Impact:** Anyone with network reach to the host can enumerate the full fleet, add/delete/disable managed arrays, rewrite the Teams webhook URL, trigger credential lookups and outbound array logins, and run generated SQL against StorMart — with no credential required and self-documenting Swagger UI to guide them.  
**Fix:** Gate all non-health routes behind get_current_user (or a global dependency) before any production exposure; wire the auth/login router; disable /docs and /redoc in production; and do not rely on network isolation alone given network_mode: host.

### [HIGH] Sorting by Severity sorts the column alphabetically, inverting true priority order
*inconsistency · confirmed · Alerts*  
**Evidence:** Alerts.tsx:175 renders the Severity header with field="severity"; handleSort defaults new sorts to 'desc' (Alerts.tsx:65-72). Backend _fetch_alerts orders by the raw severity string: ORDER BY f"{sort_by} {direction}" (backend/app/api/v1/alerts.py:53-55). Severity values are the literals 'critical'/'warning'/'info' (see collectors and Severity type in types.ts:4), which sort alphabetically critical < info < warning.  
**Impact:** Clicking 'Severity' desc lists warning, then info, then critical — burying critical alerts at the bottom exactly when the user is trying to triage by importance. The column header implies priority ordering but delivers alphabetical, misrepresenting alert urgency.  
**Fix:** Sort by a severity rank on the backend (CASE mapping critical=0/warning=1/info=2/unknown=3) rather than the string, or map before ORDER BY. Keep the desc/asc semantics meaning most/least severe.

### [HIGH] Analytics requests fleet-history with no limit; backend 5000-row cap truncates the selected time window at fleet scale
*broken · confirmed · Analytics*  
**Evidence:** frontend/src/api/arrays.ts:41-44 calls fleetHistory(hours) sending only {hours}; the backend get_fleet_history (backend/app/api/v1/analytics.py:93-101) defaults limit=5000 and _fetch_fleet_history (analytics.py:104-128) takes TOP 5000 rows ORDER BY collected_at DESC. The in-code comment at analytics.py:106-109 states ~90 arrays over 24h is ~26k rows > the cap. Analytics.tsx never overrides the limit, so the query returns only the most recent ~5000 rows.  
**Impact:** For 97 arrays the 24h/48h/7d ranges silently show only the last hour or so of data across the fleet, and any array whose only in-window samples are older than that cutoff disappears entirely from allArrayNames/visibleArrays. The header 'N of M arrays · X data points' (Analytics.tsx:161) presents this truncated subset as the complete window, so operators read a misleadingly narrow/partial time series. Gets strictly worse as the fleet grows.  
**Fix:** Have fleetHistory pass a limit sized to hours*arrayCount (or add a dedicated bucket/downsample endpoint), and/or raise/remove the cap for this query. At minimum surface when the cap is hit so the UI can warn the window is truncated.

### [HIGH] Top-N applied globally then intersected with vendor/group filter — vendor+TopN can show zero arrays
*bug · confirmed · Analytics*  
**Evidence:** Analytics.tsx:81-101 builds visibleArrays: it filters by vendor (83-86) and group (87-90), then at 96-98 applies Top-N via arrayIOPSRank.slice(0, topN). arrayIOPSRank (69-78) is ranked across ALL arrays in rawData, not the vendor/group-filtered subset. The Top-N set is intersected with the already-vendor-filtered list.  
**Impact:** If the global top-N IOPS arrays don't belong to the selected vendor/group, the intersection yields fewer than N arrays or none at all — selecting vendor=netapp + Top 10 while the global top 10 are Pure renders an empty chart, and the 'No history data yet' empty state (281-285) misleads the user into thinking there is no data.  
**Fix:** Rank within the filtered subset: apply vendor/group filters first, then compute Top-N over the remaining arrays (rank the filtered list, not arrayIOPSRank of the whole fleet).

### [HIGH] Analytics endpoints do NOT exclude disabled arrays, so fleet capacity totals drift between Dashboard and Capacity page
*inconsistency · confirmed · Backend arrays + analytics APIs*  
**Evidence:** arrays.py:_fetch_fleet_stats (lines 119-136), _fetch_array_table (172-173) and list_arrays (600-604) all explicitly exclude arrays with managed_arrays.enabled=0. But every analytics aggregation omits that filter: analytics.py:_fetch_capacity_breakdown (146-156) selects all metrics_current, _fetch_top_growers current query (395-408) and array-growth (284-289) and _fetch_array_capacity_rows/Excel export (692-710) all LEFT JOIN with no enabled predicate. stats.py:50-57 (which backs /daily-trend via daily_stats) also SUMs all of metrics_current with no enabled filter.  
**Impact:** A disabled/decommissioned array that lingers in metrics_current (the code comments cite ODCSWING) is counted in capacity-breakdown, top-growers, the Excel report, per-array growth, and the daily_stats trend, but excluded from /arrays/fleet-stats and /arrays/table. The Dashboard 'Total Capacity' tile (fleet-stats) and the Capacity page trend/breakdown will report different fleet TB whenever any array is disabled — the exact drift the fleet-stats rewrite claims to have eliminated.  
**Fix:** Apply the same `array_name NOT IN (SELECT array_name FROM managed_arrays WHERE enabled=0)` exclusion (or better, INNER JOIN managed_arrays ma ON ... AND ma.enabled=1) consistently across all analytics/stats capacity aggregations, or centralize it in one helper used by every endpoint.

### [HIGH] Single-array history returns the OLDEST rows and silently drops recent data for large time windows
*bug · confirmed · Backend arrays + analytics APIs*  
**Evidence:** analytics.py:_fetch_history (lines 26-35) uses `SELECT TOP {limit} ... FROM metrics_history WHERE collected_at >= DATEADD(HOUR,-?,GETDATE()) ORDER BY collected_at ASC`. TOP with ORDER BY ASC keeps the EARLIEST rows. _fetch_fleet_history (104-128) documents this exact bug and fixes it with an inner `TOP ... ORDER BY collected_at DESC` subquery re-sorted ASC — but _fetch_history was never given the same fix.  
**Impact:** For hours near the 720h (30-day) max, a single array produces far more samples than limit=1440 (≈288/day at 5-min cadence → ~8640 over 30d), so the endpoint returns only the oldest 1440 points and the chart is missing 'now'. Users viewing a 30-day array history see stale/truncated data with no error.  
**Fix:** Mirror the fleet-history fix: select TOP {limit} ordered by collected_at DESC in an inner query, then re-sort ASC in the outer query.

### [HIGH] Metrics collectors overwrite good capacity/perf with 0 on any partial API failure, corrupting metrics_current and the capacity-projection history
*bug · confirmed · Backend metrics collectors (pure/netapp/storagegrid)*  
**Evidence:** collect() always seeds a truthy dict (array_name/vendor/collected_at), so base.run()'s 'No data returned' guard (base.py:211) never trips even when every metric call fails. save() then writes data.get('capacity_total',0)/get('capacity_used_pct',0) UNCONDITIONALLY: pure/metrics.py:195-210, netapp/metrics.py:235-250, netapp/storagegrid_metrics.py:125-132. Capacity is only populated when the space/aggregate/metric-query call returns (pure/metrics.py:60-67, netapp/metrics.py:74-95, storagegrid_metrics.py:80-88). A single transient GET failure therefore updates metrics_current to capacity_used_pct=0 AND appends a 0 row to metrics_history. Unlike the volumes collectors, metrics have NO would_shrink_below / empty-collect guard.  
**Impact:** On this capacity-overhaul branch the server-side growth projection and /daily-trend read metrics_history; injected 0-capacity samples create artificial cliffs that poison projected-full math and make the dashboard flash 0% until the next good poll. StorageGrid is worst hit: its Prometheus metric_query can time out (30s) and both queries must succeed (storagegrid_metrics.py:82), so one slow query zeroes capacity and writes a 0 history point.  
**Fix:** Before saving, skip/keep-existing when the key metric sections are missing (mirror the volumes empty-collect guard). At minimum, do not INSERT a metrics_history row when capacity_total is unknown, and use COALESCE-to-existing rather than default-0 in the metrics_current UPDATE.

### [HIGH] Fresh-DB startup crash: volumes_cache/hosts_cache vendor migrations run before the tables are created
*broken · likely · DB init / migrations*  
**Evidence:** In init_database() the 'add vendor column' migrations for volumes_cache (session.py:494-502) and hosts_cache (session.py:505-512) execute BEFORE those tables are CREATEd (volumes_cache at :546, hosts_cache at :681). On a fresh/empty DB, OBJECT_ID('USM.volumes_cache') is NULL, so the sys.columns subquery matches nothing, IF NOT EXISTS is TRUE, and it runs `ALTER TABLE USM.volumes_cache ADD vendor ...` on a table that does not exist -> SQL error 4902. These ALTERs are NOT wrapped in try/except, and init_database() is called unguarded from the lifespan (main.py:44), so the whole transaction aborts and startup crashes.  
**Impact:** Any first-time install, disaster-recovery rebuild, or fresh test/staging DB will crash-loop on startup, taking every collector down with it. Existing production DBs are unaffected only because they already have the tables and columns (IF NOT EXISTS is FALSE), which is why it stays latent.  
**Fix:** Move the volumes_cache/hosts_cache vendor migrations to AFTER their CREATE TABLE statements, or guard each ADD-column migration with an OBJECT_ID IS NOT NULL check / try-except like the snapshots BIGINT migration already does.

### [HIGH] Alert dedup uses Python hash() for message_id — randomized per process, so every restart re-inserts and re-notifies all alerts
*bug · confirmed · Dell/Oracle/Hitachi alerts*  
**Files:** backend/app/collectors/dell/alerts.py, backend/app/collectors/oracle/alerts.py, backend/app/collectors/hitachi/alerts.py  
**Evidence:** dell/alerts.py:69 `msg_id = hash(str(alert_id)) % 2147483647`; oracle/alerts.py:65 `hash(prob.get("uuid","")) % 2147483647`; hitachi/alerts.py:67 `hash(str(alert_id)) % 2147483647` for non-int IDs. Python salts str hashing with a random per-interpreter seed (PYTHONHASHSEED). backend/Dockerfile (python:3.11-slim) sets no PYTHONHASHSEED, so the seed changes on every container start. save() dedups by `WHERE array_name=? AND message_id=?`, so the same source alert maps to a DIFFERENT message_id after each restart.  
**Impact:** After every redeploy/restart, all currently-open Dell/Oracle (and non-integer Hitachi) alerts are treated as brand-new: duplicate rows accumulate in USM.messages and every one re-fires a Teams notification — an alert storm on each deploy. Dedup only works within a single process lifetime.  
**Fix:** Replace hash() with a stable deterministic id: hashlib.blake2b/md5 of the vendor-native alert key truncated to an int (e.g. int(hashlib.md5(key.encode()).hexdigest()[:8],16)), or store the native string id in a keyed column. As a stopgap, pin PYTHONHASHSEED=0 in the Dockerfile.

### [HIGH] Multi-turn context pairs each question with the WRONG SQL (off-by-index over a filtered array)
*bug · confirmed · Frontend Chat / context building*  
**Files:** frontend/src/api/chat.ts  
**Evidence:** chat.ts:48-54 filters to user messages then maps with the FILTERED index `i`, but reads sql from the UNFILTERED array: `.filter(m=>m.role==='user').map((m,i)=>({question:m.content, sql:context?.[i+1]?.sql}))`. In a real thread [user0,asst1,user2,asst3,...], the filtered list is [user0,user2,...]; for i=0 `context[1]` is asst1 (correct), but for i=1 `context[2]` is user2 (a user msg with no sql), i=2 -> context[3]=asst3 by luck is off again after more turns. The backend consumes this directly: chat.py:729-733 appends `turn['question']` as a user msg and `turn.get('sql')` as the assistant reply to build the few-shot prompt.  
**Impact:** For every conversational turn after the first, the SQL few-shot example fed to the LLM is either missing or belongs to the wrong question, degrading Text-to-SQL quality precisely in multi-turn follow-ups (the feature this context exists to support). Silent — no error, just worse answers.  
**Fix:** Build pairs by walking the original array and pairing each user message with the immediately following assistant message's sql (e.g. iterate indices, when msg[i].role==='user' take msg[i+1]?.sql if msg[i+1]?.role==='assistant'), then slice(-4).

### [HIGH] Collection interval controls only affect Pure jobs; other 6 vendors are hardcoded out
*bug · confirmed · Frontend Settings - Collection tab*  
**Files:** frontend/src/pages/Settings.tsx, backend/app/collectors/scheduler.py  
**Evidence:** Settings.tsx:201-203 wires the three IntervalRows to fixed jobIds 'pure_metrics', 'pure_volumes', 'pure_alerts'. But scheduler.py:229-231 creates one job PER vendor per collector type: job_id = f"{vendor}_{ctype}" (netapp_metrics, hpe_metrics, hitachi_volumes, dell_alerts, ...). The UI labels them generically 'Metrics'/'Volumes'/'Alerts' (200-203) implying fleet-wide effect. settingsApi.updateJobInterval PUTs /scheduler/jobs/{jobId}/interval (settings.ts:36-37), so only the Pure scheduler jobs are ever retuned.  
**Impact:** On a 97-array / 7-vendor fleet, an operator changing 'Metrics' interval believes they throttled all collection but only touched Pure. All non-Pure vendors keep their old cadence. If 'pure_alerts' etc. don't exist for a given deployment the PUT 404s. This gets worse as more vendors are added.  
**Fix:** Enumerate scheduler jobs from /scheduler/status and render an interval row per actual job id (grouped by vendor/type), or add a backend endpoint that sets an interval for all {*}_{ctype} jobs. Stop hardcoding the 'pure_' prefix.

### [HIGH] Hitachi volumes collection is N+1 and blocking (~370s) — per-host-group WWN calls plus 180s/90s serial timeouts
*inefficiency · confirmed · Hitachi volumes*  
**Files:** backend/app/collectors/hitachi/volumes.py  
**Evidence:** volumes.py:52 LDEV query `timeout=180`; :121 host-groups `timeout=90`; then :130-133 issues one `host-wwns?portId=..&hostGroupNumber=..` GET (timeout=15) per host group inside the loop over up to 500 host groups — a classic N+1. All calls are sequential/blocking in the collector thread.  
**Impact:** With many host groups the enrichment loop alone can run into minutes; combined with the 180s+90s timeouts this produces the observed ~370s runtimes. Each run holds one of only 16 shared ThreadPoolExecutor workers (scheduler.py:31) for that whole time, so a handful of slow Hitachi arrays starve every other vendor's collectors — and it worsens linearly at 2-5x scale.  
**Fix:** Fetch all host-WWNs in bulk (single host-wwns query per port, or the host-groups detail expansion) instead of one call per host group; cap the LDEV/host-group timeouts and parallelize enrichment; consider a dedicated slow-vendor pool.

### [HIGH] Age-based auto-resolve and alert purge use TRY_CAST(opened AS DATETIME2) on timezone-bearing timestamps, which returns NULL — both silently no-op
*bug · likely · NetApp alerts auto-resolve + scheduler alert cleanup*  
**Evidence:** netapp/alerts.py:178 and scheduler.py:348 filter with TRY_CAST(opened AS DATETIME2) < DATEADD(...). But `opened` is stored as the raw vendor timestamp: NetApp uses EMS evt['time'] (netapp/alerts.py:92, ISO8601 with a timezone offset/Z) and Pure uses msg['opened'] (also ISO8601 Z). SQL Server TRY_CAST of an offset/Z-bearing string to DATETIME2 yields NULL (it requires DATETIMEOFFSET), so the predicate is NULL<... = false for every row.  
**Impact:** NetApp alerts are never auto-resolved (BUG-04 fix is inert) and the nightly resolved-alert purge matches nothing, so the messages table grows unbounded and NetApp criticals never clear. Compounds the Pure-never-resolves finding.  
**Fix:** Cast via DATETIMEOFFSET (TRY_CAST(opened AS DATETIMEOFFSET)) or TRY_CONVERT with the right style, or store a real datetime column at insert time. Verify against actual stored `opened` values before shipping.

### [HIGH] Pure alerts never get resolved — open-only fetch with no resolution path means stale 'active' alerts and unbounded messages growth
*bug · confirmed · Pure alerts collector*  
**Evidence:** collect() fetches only open messages: self.client.get('message', params={'open':'true'}) (pure/alerts.py:45). Once an alert closes on the array it drops off that list and is never seen again, so its row is never updated. The only resolve trigger is resolved=CASE WHEN closed... (pure/alerts.py:88), which can only fire while the message is STILL in the open list. There is no age-based auto-resolve for Pure (grep confirms only netapp/alerts.py:167 and services/capacity_alerts.py resolve rows). alert_cleanup purges only resolved=1 (scheduler.py:347).  
**Impact:** Pure alert rows stay resolved=0 forever after the array clears them: the Alerts page and fleet stats (stats.py counts resolved) show long-cleared criticals as active, and because cleanup only deletes resolved=1, Pure messages accumulate without bound over time.  
**Fix:** Add a Pure age/absence-based auto-resolve: mark messages resolved when they no longer appear in the latest open set for the array (diff current open message_ids vs stored open rows), analogous to netapp _auto_resolve_old_alerts.

### [HIGH] NotesCell blur handler unconditionally re-saves on Escape and fires duplicate PATCH on Enter
*bug · likely · Volumes notes editing*  
**Files:** frontend/src/pages/Volumes.tsx  
**Evidence:** In NotesCell (Volumes.tsx:171-178): handleKey does `if (e.key === 'Escape') setEditing(false)` and `if (e.key === 'Enter') mutate()`. onBlur is wired to `handleBlur` which unconditionally calls `mutate()` (176-178). Pressing Escape sets editing=false, which unmounts the focused input and triggers a blur -> handleBlur -> mutate(), so Escape SAVES the edited value instead of cancelling it. Pressing Enter calls mutate(); on success it sets editing=false (159-162), unmounting the input and firing blur -> mutate() a second time. There is also no dirty-check: simply focusing a note and clicking away (no edit) issues a PATCH write plus a full `['volumes']` invalidation/refetch.  
**Impact:** Escape does not cancel edits (data-loss/surprise for users); Enter double-writes; every focus+blur triggers a needless DB write and a full volumes refetch. At scale the redundant invalidations add unnecessary load.  
**Fix:** Track a committed baseline and only mutate when value actually changed. Use a `savedRef`/flag so Escape sets a 'cancel' flag that suppresses the blur-save, and after Enter's mutate suppress the subsequent blur. E.g. onBlur should not fire mutate if the value equals volume.notes or if an Escape/Enter just handled it.

### [HIGH] SELECT ... INTO is a mutation that bypasses the SQL safety validator
*risk · confirmed · backend chat / sql_safety*  
**Evidence:** backend/app/services/sql_safety.py:13-23 _BLOCKED_PATTERNS blocks DROP/DELETE/UPDATE/INSERT/ALTER/CREATE/TRUNCATE/MERGE and 'INTO OUTFILE'/'LOAD DATA' (MySQL forms) but NOT bare T-SQL 'SELECT ... INTO newtable'. validate_sql (line 66) only checks that the statement starts with SELECT/WITH, which 'SELECT * INTO USM.evil FROM USM.metrics_current' satisfies. None of the blocked keywords (CREATE etc.) appear in a SELECT..INTO, and the INTO target is not a FROM/JOIN target so the table-allowlist check at line 77-89 never sees it. It passes validation and executes, creating/writing a table.  
**Impact:** The read-only guarantee is breakable. A crafted question (or a hallucinating LLM) can get 'SELECT ... INTO <table>' past the safety filter and create/populate tables in the DB, i.e. a write in a pipeline whose entire premise is read-only. This is exactly the class of attack the validator exists to stop.  
**Fix:** Add a blocked pattern for T-SQL SELECT-INTO, e.g. r'\bINTO\s+(?!OUTFILE)\w' or explicitly r'\bSELECT\b[\s\S]*?\bINTO\b' (careful not to match 'INSERT INTO' which is already blocked). Simpler: reject any query containing '\bINTO\b' that is not part of an already-blocked keyword.

### [HIGH] add_safety_limits produces invalid T-SQL for DISTINCT queries (TOP inserted before DISTINCT)
*bug · confirmed · backend chat / sql_safety*  
**Evidence:** backend/app/services/sql_safety.py:121-133 add_safety_limits does re.sub(r'\bSELECT\b', 'SELECT TOP 100', sql, count=1). For 'SELECT DISTINCT app_acronym FROM ...' this yields 'SELECT TOP 100 DISTINCT app_acronym ...'. In SQL Server the grammar is SELECT [ALL|DISTINCT] [TOP (n)] — DISTINCT must precede TOP — so 'SELECT TOP 100 DISTINCT ...' is a syntax error ('Incorrect syntax near DISTINCT'). The documented CMS query patterns the model is told to emit are all DISTINCT (chat.py:173 'SELECT DISTINCT app_acronym...', chat.py:175, chat.py:294-297 area).  
**Impact:** Every 'which applications/databases are on array X' style query (the whole CMS feature) is mangled into invalid SQL, fails execution, and burns a full _fix_sql LLM round-trip. If the LLM's fix reproduces a bare DISTINCT (no explicit TOP), add_safety_limits breaks it again and the user gets 'SQL error (even after retry)'. Best case it wastes the most expensive step; worst case the query never succeeds.  
**Fix:** Insert TOP after any leading DISTINCT/ALL: match r'\bSELECT\b(\s+(?:ALL|DISTINCT))?' and place 'TOP 100' after the optional DISTINCT group. Also skip insertion when DISTINCT+TOP already present.

### [HIGH] Teams notification silently suppressed when a capacity alert re-opens after resolving
*bug · confirmed · capacity_alerts*  
**Files:** backend/app/services/capacity_alerts.py  
**Evidence:** backend/app/services/capacity_alerts.py:104-110 `_upsert_alert` on an existing row sets `resolved=0, closed=NULL` but does NOT reset `teams_notified`. `_should_notify` (:72-85) only checks the age of `teams_notified`. In `check_array_thresholds` (:207) and `check_fleet_projection` (:270) the notify decision uses `_should_notify(existing)` where `existing` still carries the old, recent `teams_notified`.  
**Impact:** An array that crosses 90%, alerts+notifies, drops below (auto-resolves), then re-crosses within CAPACITY_ALERT_RESEND_DAYS (default 7d) re-opens the alert row but sends NO Teams notification. A flapping array near a threshold, or the fleet projected-full alert re-firing, is invisible to operators for up to a week. `opened` is even incremented so metrics claim it fired.  
**Fix:** On re-open (transition resolved=1 -> 0) clear `teams_notified` in `_upsert_alert`, or in `_should_notify` treat a freshly re-opened alert (was resolved) as new and always notify.

### [HIGH] API has no authentication and ships an insecure default SECRET_KEY
*risk · confirmed · config*  
**Files:** backend/app/core/config.py, backend/app/api/v1/settings.py  
**Evidence:** backend/app/core/config.py:41 `secret_key` default `"CHANGE_ME_BEFORE_PRODUCTION_USE"` with comment 'auth not enforced yet'. Endpoints like backend/app/api/v1/settings.py:117 `POST /capacity-alerts/run`, :126 `PUT /notifications` (rewrites the Teams webhook URL), and :235 inventory sync trigger have no `Depends(...)` auth guard.  
**Impact:** Anyone who can reach the API can read the full multi-vendor storage inventory and capacities, rewrite the Teams webhook URL (redirect/suppress all alerts), and trigger syncs/alert runs. There is no key rotation or validation that the placeholder SECRET_KEY was changed. At 2-5x scale / broader network exposure this is a direct security exposure.  
**Fix:** Add auth (even a shared token / reverse-proxy auth) before mutating endpoints; fail startup if SECRET_KEY is still the placeholder in non-debug mode; gate the webhook-rewrite and trigger endpoints behind it.

### [HIGH] /hosts/storage-report is an N+1 loop, not the "single JOIN" its docstring claims
*inefficiency · confirmed · hosts API*  
**Evidence:** backend/app/api/v1/hosts.py:142-156. The docstring (line 135) says "Uses an optimized SQL approach: bulk-fetch hosts, then single JOIN to volumes" and the inline comment (line 142) says "Bulk-fetch all matching hosts in one query using OR conditions", but the code loops `for srv in batch:` and runs one `cursor.execute(... WHERE UPPER(host_name) LIKE UPPER(?) + '%')` per server. The outer batching by 50 (line 147) is cosmetic — it never combines servers into one query. A report over N servers issues N sequential round-trips.  
**Impact:** A report for hundreds of servers fires hundreds of sequential queries on the single uvicorn worker's threadpool, serializing the request and holding a DB connection for its duration. Scales linearly with input size; at 2-5x fleet growth this becomes a multi-second blocking endpoint.  
**Fix:** Actually batch: build one query per chunk with OR'd prefix predicates (as already done for volume sizes in step 3, lines 180-195), or better, insert the server list into a TVP/temp table and JOIN once. Remove the misleading docstring/comment.

### [HIGH] /hosts/storage-report prefix-LIKE over-matches and double-counts shared volumes
*bug · confirmed · hosts API*  
**Evidence:** backend/app/api/v1/hosts.py:151-169, 222-233. Match is `WHERE UPPER(host_name) LIKE UPPER(?) + '%'`, so server "abc" also matches hosts "abc01", "abcd", etc. Every matched host's volumes are appended to a plain list: `all_host_data[srv]["vol_keys"].append((array_name, vn))` (line 169). Aggregation then iterates that list (line 222) and increments `vol_count`/provisioned/used for every occurrence. If two matched hosts on the same array share a volume (common with clustered/HA hosts), the (array,volume) key appears twice and is counted twice. Additionally, unescaped `%`/`_`/`[` in a server name are treated as LIKE wildcards.  
**Impact:** Inflated volume_count and total_provisioned/used_bytes in the capacity report — silently wrong numbers that feed capacity planning. Over-broad prefix matching can also attribute another server's storage to the queried server.  
**Fix:** Deduplicate vol_keys into a set per server before aggregating; use exact match (or an explicit, escaped pattern) instead of unbounded prefix LIKE; add ESCAPE handling for wildcard characters.

### [HIGH] Inventory sync aborts entirely on a single row with NULL Vendor or NULL ArrayNameKey
*bug · confirmed · inventory*  
**Files:** backend/app/services/inventory.py  
**Evidence:** backend/app/services/inventory.py:110 `array_name = row.get("ArrayNameKey", "").strip()` and :115 `usm_vendor = _VENDOR_MAP.get(dim_vendor, dim_vendor.lower().replace(" ", ""))`. Both run BEFORE the per-row try/except (which begins at :136). `.get(col, default)` returns None (not the default) when the column exists but the value is SQL NULL, so `None.strip()` and `None.lower()` raise AttributeError. Note the default arg `dim_vendor.lower()...` is also eagerly evaluated even when the key is found.  
**Impact:** One DimStorageFinance row with a NULL Vendor or NULL ArrayNameKey raises an uncaught AttributeError that propagates out of `sync_from_dim_storage_finance`, aborting the whole nightly 03:00 sync. Arrays processed earlier in the loop were already committed (per-row transactions), so inventory is left half-synced with no clean error path.  
**Fix:** Coalesce NULLs before use: `array_name = (row.get("ArrayNameKey") or "").strip()` and compute `dim_vendor = row.get("Vendor") or "unknown"` on its own line before the `_VENDOR_MAP.get(...)` call; skip/continue rows with an empty array_name. Consider wrapping the whole loop body in the try.

### [HIGH] KeePass background refresh becomes a tight retry storm when KeePass is unreachable
*risk · confirmed · keepass*  
**Files:** backend/app/services/keepass.py  
**Evidence:** backend/app/services/keepass.py:159-176. The loop selects keys where `exp - now <= _REFRESH_BEFORE` (3600s) and, on fetch failure (:173-176), does NOT update the cache entry's expiry (comment defers that to get_credentials). So a failing key keeps satisfying the due-predicate and is retried every 60s indefinitely. Each `_fetch_from_keepass` blocks up to `_FETCH_TIMEOUT`=15s (:34,:48) and the keys are fetched sequentially (:167-176).  
**Impact:** If KeePass is down, every cached key (~1 per array, up to ~97) is retried each 60s wake. With 15s timeouts sequentially that is up to ~24 min of blocking per iteration, so the loop falls permanently behind and hammers KeePass with a continuous request storm rather than backing off.  
**Fix:** On refresh failure, push the entry's expiry forward (backoff) so it isn't immediately due again, cap attempts, and/or bound total time per wake. Add jitter/backoff and stop retrying a key more than once per wake.

## Systemic patterns

- Silent truncation via capped/unpaginated queries pervades both tiers — analytics fleet-history 5000-row cap, single-array history TOP-ASC, NetApp get_all 5000, Hitachi count=8192, Dell per_page, NetApp EMS 200/poll, and frontend caps (50 newest alerts, 200 drilldown rows, 500 volume picker) — all presenting a partial subset as the complete truth.
- Alert lifecycle never closes the loop across every vendor: collectors insert/update but almost none mark cleared alerts resolved, and the two mechanisms that should (NetApp auto-resolve + nightly purge) are inert due to TRY_CAST(string timestamp)→NULL, so USM.messages grows unbounded and stale alerts read as active.
- Partial failures overwrite or zero good data instead of preserving it (metrics zeroing capacity on one failed GET; one bad alert row rolling back the whole per-array batch; inventory sync aborting on one NULL row) — the opposite of fail-safe.
- Timestamps stored as NVARCHAR strings recur as a root cause: non-sargable nightly purge, TRY_CAST→NULL auto-resolve, lexicographic MAX in the DB-info page, and naive-local vs GETDATE() vs UTC-scheduler clock skew in resend/projection windows.
- N+1 / connection-per-row patterns everywhere (storage-report per server, inventory upsert per array, capacity_alerts per array holding a connection across a 10s Teams POST, Hitachi/Oracle deep serial collectors) despite a set-based batch_upsert helper already existing — each scales linearly with fleet size.
- Read-only, notification, and safety guarantees are enforced by prompt/convention rather than code: SELECT-INTO and comma-joins slip the SQL validator, TEAMS_SEVERITIES filtering is dead, CHAT_QUERY_TIMEOUT is never applied, and AUTH_REQUIRED is referenced but unimplemented.
- Unit and identity conventions are inconsistent: TiB is labeled TB and base-1024 fights base-1000; vendor identity drifts (StorageGrid persisted as netapp, frontend enum omits active vendors, row mappers default to 'pure' vs schema 'unknown', two divergent valid-vendor sets).
- Two divergent code paths compute the 'same' number differently (fleet-stats vs /table anchoring; weighted vs unweighted utilization; disabled-array filter applied inconsistently), so headline figures disagree by construction.
- Heavy copy-paste duplication that has already drifted: SortHeader x4 (one with reordered props), _safe_json/_TIB/pagination-envelope/StorageGrid-remap/linreg-slope reimplemented, and the router fetch/paginate/sort block triplicated — a fix in one copy silently misses the others.
- Missing error handling is uniform in shape: frontend mutations are onSuccess-only and queries ignore isError (outages render as healthy); backend uses bare except/pass that degrades silently (e.g. dashboard falling back to arrays.txt on a DB error).

## Scale-readiness gaps

- Single uvicorn worker as actually deployed (the RUN_SCHEDULER api/collector split exists in code but the shipped compose runs one process) is a hard ceiling — a collection storm or a couple of slow chat calls starves all API traffic and flips the container unhealthy.
- The shared 16-thread collector pool is the primary contention point: slow serial vendor collectors (Hitachi ~370s, Oracle deep N+1) hold a worker for minutes and maintenance jobs share the same pool, producing head-of-line blocking that gets strictly worse as arrays grow 2-5x.
- N+1 connection-per-array patterns (inventory sync, capacity threshold checks, host storage-report) create connect/commit/close churn that scales linearly with fleet size and competes with collector writes for SQL Server connections.
- Analytics fleet-history's 5000-row cap is already exceeded at 90 arrays over 24h (~26k rows) per the code's own comment, so 24h/48h/7d windows silently show only the most recent slice today and degrade further at scale.
- No composite (array_name, collected_at) index on metrics_history forces scan-and-sort for the ROW_NUMBER/window-function analytics (top-growers, array-growth), driving large sort/spool and tempdb pressure on the single worker as history accumulates.
- Unbounded USM.messages growth: alerts never resolve and the purge is inert (TRY_CAST NULL), so the table grows without limit and the nightly cleanup can't reclaim it — plus volumes_history is already ~41.8M rows / 5.5GB and the admin DB-info page runs exact COUNT(*) without NOLOCK (and omits that largest table).
- Frontend at fleet scale: no route code-splitting (single ~800KB bundle), no virtualization (hundreds of rows re-rendered every 60s plus a duplicate copy in the modal), a global 60s refetchInterval + unfiltered Navbar invalidateQueries thundering-herd against the single worker, and only 20 line colors for up to 97 arrays.
- Silent inventory truncation caps (NetApp 5000, Hitachi unpaginated 8192, Dell per_page, 500-volume picker) hide progressively more data as array and volume counts grow, with the stable-count shrink-guard blind to it.
- Deep OFFSET pagination with no max offset on the largest cache tables degrades on late pages, and KeePass refresh (per-key sequential 15s fetches, no backoff/single-flight) falls behind as key count scales.

## Remediation architecture & roadmap

### Guiding principles

- Fail-safe over fail-silent: a monitoring platform must never overwrite good data with zeros/defaults or render an outage as healthy. On partial collector failure preserve last-known values; on a backend error surface it, don't fall through to 'all clear'/green.
- Enforce guarantees in code, not in prompts or convention. Auth, SQL read-only-ness, TEAMS_SEVERITIES filtering, and the enabled=1 fleet filter must all be code paths that cannot be bypassed, never comments or LLM instructions.
- One source of truth per number. Capacity aggregation, unit conversion (TiB vs TB, base-1024 vs base-1000), vendor identity, and pagination shaping each get exactly one shared helper; divergent copies are the root cause of headline figures disagreeing by construction.
- Deploy the architecture the code already supports. The RUN_SCHEDULER api/collector split exists in config.py/main.py but is not in the shipped compose; scaling work is mostly wiring, not rewrites.
- Set-based, bounded, and paginated by default. No connection-per-row, no N+1, no unbounded query, and no silent cap that presents a partial subset as the whole truth — every limit is explicit and logs when hit.
- Type the boundaries. Timestamps are DATETIME2/DATETIMEOFFSET columns not NVARCHAR; the frontend-backend contract is generated/shared types not conventions; migrations are versioned not inline-on-boot DDL.
- Sequence strictly by blast radius: close the unauthenticated-mutation hole and stop numeric corruption before any polish. Make failures visible before making them rare, and make them rare before making the platform pretty.
- Observability and auth are prerequisites for the 2-5x / multi-user / LDAP future, not Phase-3 nice-to-haves layered on afterward.

### Quick wins (high value, low effort)

- **Lock down the exposed surface** _(~1 hour)_ — Removes the self-documenting attack map on an unauthenticated host-networked API and prevents a forgeable-JWT foundation before auth even ships.
  - *How:* Disable /docs and /redoc when not in debug (FastAPI(docs_url=None,redoc_url=None)); in core/config.py fail startup with a hard exception if SECRET_KEY is still 'CHANGE_ME_BEFORE_PRODUCTION'. ~10 lines in main.py/config.py.
- **Pin PYTHONHASHSEED=0** _(~1 line)_ — Immediately stops the Dell/Oracle/Hitachi alert storm that re-inserts and re-notifies every open alert on each container restart/deploy.
  - *How:* Add PYTHONHASHSEED=0 to the usm-backend environment block in docker/docker-compose.yml (and Dockerfile ENV). Stopgap until stable content-hash ids land.
- **Stop metrics from zeroing capacity** _(~half day)_ — Eliminates the artificial cliffs that poison growth projections and flash 0% on the dashboard from a single transient GET.
  - *How:* In the metrics collectors, COALESCE-to-existing in the metrics_current UPDATE and skip the metrics_history INSERT entirely when capacity_total is unknown (mirror the existing volumes empty-collect guard).
- **Reset teams_notified on alert re-open** _(~1 line)_ — Restores Teams notifications for flapping/re-firing capacity alerts that currently go silent for up to 7 days.
  - *How:* In services/capacity_alerts.py _upsert_alert, set teams_notified=0 on the resolved 1->0 transition.
- **Apply the enabled=1 filter everywhere via the shared helper** _(~half day)_ — Makes Dashboard and Capacity page report the same fleet TB; kills disabled/decommissioned arrays leaking into capacity-breakdown, top-growers, daily trend, and the Excel report.
  - *How:* Route all analytics/stats capacity aggregations through one helper that INNER JOINs managed_arrays ma ON ... AND ma.enabled=1, the same exclusion fleet-stats already uses.
- **Fix the two inert alert-lifecycle predicates** _(~1 hour)_ — Unblocks NetApp auto-resolve and the nightly purge so USM.messages stops growing unbounded and cleared criticals stop showing as active.
  - *How:* Cast timestamps via DATETIMEOFFSET (not TRY_CAST(... AS DATETIME2) on tz-bearing strings, which returns NULL) in both the auto-resolve and purge predicates.
- **Surface errors on the two most dangerous queries** _(~half day)_ — Prevents a backend outage from rendering as 'all clear' with a green icon — the worst failure mode for a monitoring dashboard.
  - *How:* Handle isError on the fleet-stats and active-alerts queries (inline error badge) instead of falling through to zero/green; add a QueryCache onError global toast.
- **Unblock fresh-DB startup** _(~1 hour)_ — Stops any first install / DR rebuild / staging DB from crash-looping and taking every collector down.
  - *How:* Move the volumes_cache/hosts_cache vendor ALTERs to after their CREATE TABLE, or guard each with OBJECT_ID(...) IS NOT NULL like the existing BIGINT migration.
- **Make charts and sorts tell the truth** _(~1 hour)_ — Outages render as breaks not smooth lines, and criticals sort to the top when triaging.
  - *How:* Remove the connectNulls prop on analytics lines; sort Severity by a CASE rank (critical=0/warning=1/info=2) instead of alphabetically.
- **Enforce the chat SQL timeout and lower-bound limits** _(~1 hour)_ — Stops arbitrary LLM SQL from running to the 60s connection timeout and stops negative limit params reaching SQL (negative TOP -> 500).
  - *How:* Actually apply the configured CHAT_QUERY_TIMEOUT to the chat query execution; add ge=1 to the limit query params in the analytics/history routers.

### Phase 1 — Stabilize (close the hole, stop the corruption, make failures visible)
_Eliminate the unauthenticated-mutation exposure, stop the platform from silently corrupting the numbers it exists to report, and ensure outages render as outages — all with minimal, low-risk changes and no re-architecture._

- Gate every non-health route behind a global auth dependency; wire the login router; disable /docs/redoc in prod; hard-fail startup on the placeholder SECRET_KEY (Auth & secret baseline).
- Make metrics collectors fail-safe: COALESCE-to-existing on metrics_current, skip metrics_history insert when capacity_total is unknown.
- Apply the enabled=1 filter to all analytics/stats capacity aggregations through one shared helper so Dashboard and Capacity agree.
- Fix single-array history (inner ORDER BY DESC / outer ASC) and add ge=1 lower bounds to limit params.
- Fix the two inert alert-lifecycle predicates (DATETIMEOFFSET cast) and add absence-based auto-resolve to every vendor collector.
- Pin PYTHONHASHSEED=0 now; replace hash() message_id with a stable content hash and widen to BIGINT; make per-array alert upsert per-row-tolerant.
- Reset teams_notified on alert re-open; add retry/backoff to the Teams POST.
- Move/guard the volumes_cache/hosts_cache ALTERs so fresh-DB/DR startup no longer crash-loops.
- Surface isError on fleet-stats and active-alerts; remove connectNulls; sort Severity by rank.
- Block SELECT-INTO and apply CHAT_QUERY_TIMEOUT as an immediate SQL-safety stopgap.
- Coalesce NULLs and wrap the row loop in the nightly inventory sync so one bad row can't abort it.

### Phase 2 — Harden & Scale (ready for 2-5x arrays and multiple users)
_Remove the single-worker ceiling and the linear-scaling contention points, replace silent truncation with real pagination, and type the boundaries so the platform holds at ~485 arrays and concurrent users._

- Deploy the collector/API split: one collector container (RUN_SCHEDULER=true) + a multi-worker API container behind nginx.
- Give slow vendors (Hitachi, Oracle) a dedicated bounded executor; bulk-fetch and parallelize their N+1 enrichment; cap per-array timeouts.
- Implement real get_all pagination per vendor; log/raise on cap hits; port host-fallback to HPE/Hitachi/Dell/Oracle; honor Pure cred_key; persist StorageGrid under its own vendor.
- Convert N+1 sites to batch_upsert / single set-based JOINs (storage-report batched + exact matching + shared-volume dedup; inventory upsert; capacity threshold check off the per-array connection).
- Adopt a migration framework (Alembic/schema_version), move DDL out of boot, migrate NVARCHAR timestamps to typed datetimes, add the composite metrics_history index, standardize NOLOCK, unify COUNT+page.
- Replace fleet-history's 5000-row cap with time-window + downsampling; add KeePass refresh backoff + single-flight.
- Harden text-to-SQL with sqlglot (allowlist enforcement, DISTINCT/CTE limit fix, timeout budget aligned to the client).
- Generate/share frontend types from the backend schema; validate the update vendor field; add onError to all mutations; fix NotesCell; add verify timeout.
- Frontend scale: route code-splitting, table virtualization, placeholderData, defined brand shades, full-fleet color scale.

### Phase 3 — Platform (LDAP, RBAC, observability, polish)
_Turn the hardened service into a multi-user platform: real identity, role-based mutation control, first-class observability, and the DRY/UX cleanups that keep it maintainable._

- LDAP bind + group->role mapping and RBAC behind the Phase-1 auth seam; elevated roles required for array mutations, webhook rewrite, and chat SQL.
- Audit logging of mutations and of DGX off-host egress (log that result rows leave, not just the question).
- Structured logging + metrics endpoint (per-collector last-success/duration/rows/truncation, DB pool, scheduler lag); replace bare except/pass; global frontend error surface.
- DRY consolidation: one SortHeader, one _safe_json/_TIB/pagination/remap module, one capacity-color threshold, dedup the pivot useMemos.
- Unit/identity correctness end-to-end: pick TiB-or-TB convention in one constant, align formatBytes/formatTB bases, correct labels, fix vendor-identity defaults.
- Modal a11y (Escape/focus-trap/scroll-lock/ARIA), truncation indicators on drilldown/volume-picker, single-root-ErrorBoundary replaced with per-route boundaries.
- Retire the write-only SQLite mirror if no reader emerges.

### Scale strategy

The scale story is less about rewrites than about deploying and enforcing what the codebase already half-implements, in risk order.

Single-worker ceiling: The highest-leverage move requires no code change. config.py/main.py already support RUN_SCHEDULER, but the shipped docker/docker-compose.yml runs one uvicorn worker coupling API, scheduler, and chat, so a collection storm or two slow chat calls starves all API traffic and flips the container unhealthy. Phase 2 splits compose into one collector container (RUN_SCHEDULER=true, single replica to avoid duplicate collection) and a multi-worker API container behind the existing nginx. This alone converts the hard ceiling into horizontal headroom for the 97->485 array growth and concurrent users.

Collector concurrency: The shared 16-thread ThreadPoolExecutor is the primary contention point — Hitachi (~370s deep N+1) and Oracle (deep unbounded N+1) each pin a worker for minutes and starve every other vendor, and maintenance jobs plus load_arrays share the same pool. The fix is a dedicated bounded executor for slow vendors with capped per-array timeouts, plus killing the N+1 itself (Hitachi host-WWNs bulk-fetched per port instead of per host group, parallelized enrichment). Silent truncation compounds this: NetApp get_all 5000, Hitachi unpaginated count=8192, Dell per_page, EMS 200/poll all present a partial subset as truth, and because the truncated count is stable the shrink-guard never fires — so real pagination with a raise/log on cap-hit is a correctness fix that only matters more at scale.

DB read patterns: N+1 connection-per-row patterns scale linearly and compete with collector writes for SQL Server connections — storage-report opens one query per server (and over-matches with prefix-LIKE, double-counting shared volumes), inventory upserts per array, and the capacity check holds a pooled connection across a 10s Teams POST. All of these have a set-based batch_upsert already available to model against. Reads also need the composite (array_name, collected_at) index to stop window-function analytics from scan-and-sort, NVARCHAR timestamps migrated to typed datetimes for sargability, consistent NOLOCK on collector-mutated cache tables, and COUNT+page in one transaction. The fleet-history 5000-row cap (already exceeded at 90 arrays over 24h) is replaced by time-window + downsampling. And unbounded USM.messages growth is closed at the source by the alert-lifecycle fixes rather than relying on the (currently inert) purge.

Error-handling standardization: The uniform failure shape — frontend mutations onSuccess-only, queries ignoring isError, backend bare except/pass degrading silently — is the most dangerous property for a monitoring tool, because it renders outages as healthy. The standard becomes: every mutation has onError (surface + revert), the two headline queries (fleet-stats, active-alerts) show an error state instead of falling through to 'all clear'/green, and backend swallowed exceptions become logged, typed handling. Fail-safe collectors (keep-existing on partial failure, never insert history without capacity_total) are the same principle applied to the write path.

Frontend-backend contract: Contracts are convention-only and already drifted (vendor enum omits active vendors, the dead PaginatedResponse schema contradicts the real wire shape, mappers default vendor differently than the schema). The strategy is to generate frontend types from the backend OpenAPI/schema so these cannot drift, validate the update vendor field server-side, and standardize response shaping. At the same time the frontend must stop being O(fleet) on the client: code-splitting off the ~800KB bundle, virtualized tables, placeholderData across sort/filter/range, a defined brand palette and a color scale covering the full fleet, and taming the 60s global refetchInterval + unfiltered Navbar invalidateQueries thundering herd against the (now multi-worker) API.

Observability and auth as prerequisites: Before LDAP and multi-user land, two seams must exist. Auth goes in first (Phase 1) as a global dependency gating every non-health route, with /docs disabled and a non-placeholder SECRET_KEY enforced at startup — LDAP/RBAC then drops in behind that same seam in Phase 3 without touching route code. Observability (per-collector success/latency/truncation, DB pool, scheduler lag, structured logs, audit of mutations and DGX off-host egress) is what makes it safe to actually run multiple workers and 5x the fleet, so it is treated as required instrumentation for the scale-up rather than post-hoc polish.

## Appendix — all findings by area

### Alerts

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | inconsistency | Sorting by Severity sorts the column alphabetically, inverting true priority order |  |
| low | tech-debt | SortHeader is duplicated byte-for-byte across four pages |  |
| low | inconsistency | Severity filter pills omit 'unknown', which the type and backend default both produce |  |
| low | ux | Opened timestamp rendered as raw backend string, unformatted |  |

### All four alerts

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Vendor alert collectors never auto-resolve cleared alerts — messages grow unbounded and dashboards show stale 'active' alerts | backend/app/collectors/hitachi/alerts.py, backend/app/collectors/dell/alerts.py, |

### All four clients

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | broken | No host-fallback loop — HPE/Hitachi/Dell/Oracle bind a single host at construction and never retry an alternate address | backend/app/collectors/hpe/client.py, backend/app/collectors/hitachi/client.py,  |

### Analytics

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | broken | Analytics requests fleet-history with no limit; backend 5000-row cap truncates the selected time window at fleet scale |  |
| high | bug | Top-N applied globally then intersected with vendor/group filter — vendor+TopN can show zero arrays |  |
| medium | risk | connectNulls bridges gaps on every line, hiding array outages / missing metrics |  |
| medium | risk | Top-N ranks by summed IOPS over the window, biasing toward denser-sampled arrays |  |
| medium | inconsistency | Latency charts display only the Top-IOPS arrays, so worst-latency arrays can be hidden |  |
| medium | ux | Only 20 line colors for up to 97 arrays — lines and legend become indistinguishable |  |
| medium | inefficiency | Three near-duplicate pivot useMemos with linear visibleArrays.includes() inside the hot loop |  |
| low | inconsistency | arrays query lacks staleTime in Analytics, inconsistent with Alerts |  |

### App.tsx / ErrorBoundary

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | broken | Single root ErrorBoundary takes down the whole shell on any page crash; fallback claims the opposite |  |

### App.tsx / vite.config.ts

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | No code splitting — every page is eagerly imported into one bundle; no manualChunks |  |

### Backend arrays + analytics APIs

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | inconsistency | Analytics endpoints do NOT exclude disabled arrays, so fleet capacity totals drift between Dashboard and Capacity page |  |
| high | bug | Single-array history returns the OLDEST rows and silently drops recent data for large time windows |  |
| medium | bug | _row_to_managed drops valid vendors (storagegrid/ibm/veritas) to 'unknown' via a stale local whitelist |  |
| medium | inconsistency | fleet-stats and /table per-array counts can disagree despite docstring guaranteeing they sum |  |
| medium | inconsistency | Fleet utilization uses an unweighted average of per-array percentages, inconsistent with capacity-weighted utilization elsewhere |  |
| medium | inefficiency | Metrics_history lacks a composite (array_name, collected_at) index; trailing-window ROW_NUMBER analytics scan-and-sort and degrade at scale |  |
| low | inconsistency | Inconsistent NOLOCK usage across current-state reads; live list/detail/history omit the hint others rely on |  |
| low | risk | /analytics/history limit parameter has no lower bound, allowing TOP 0 / negative TOP that errors or returns nothing |  |
| low | inconsistency | Bytes divided by 2^40 (TiB) but reported as 'TB' throughout the capacity APIs |  |
| low | tech-debt | ManagedArrayUpdate.vendor is an unvalidated free-form string while create/list validate against VendorType |  |
| low | inefficiency | _load_array_meta reloads the entire managed_arrays table on every list/detail request, including single-array detail |  |
| low | inconsistency | data_reduction default value is inconsistent across endpoints (1.0 vs 0.0) |  |

### Backend metrics collectors (pure/netapp/storagegrid)

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Metrics collectors overwrite good capacity/perf with 0 on any partial API failure, corrupting metrics_current and the capacity-projection history |  |

### Collector base / logging

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | tech-debt | Per-array loggers each attach a FileHandler to a shared per-vendor logfile, writing concurrently from the thread pool |  |

### Cross-collector capacity math

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Capacity_total semantics differ per vendor while sharing one column and (for StorageGrid) one vendor label |  |

### DB / batch_upsert

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | bug | batch_upsert applies extra_where to the existing-key SELECT but not to the stale DELETE, contradicting its docstring |  |

### DB / partial-collect guard

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | would_shrink_below early-return produces a bogus existing_count of -1 |  |

### DB init / async

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | Unconditional schema-altering migration and deprecated get_event_loop on every run |  |

### DB init / migrations

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | broken | Fresh-DB startup crash: volumes_cache/hosts_cache vendor migrations run before the tables are created |  |

### Dell volumes

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Dell host↔volume linkage uses internal IDs while volumes are keyed by name; volume 'hosts' list is computed then discarded | backend/app/collectors/dell/volumes.py |

### Dell volumes/alerts

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Dell collectors are unpaginated — LUNs/filesystems truncated at per_page=2000, alerts at per_page=100 | backend/app/collectors/dell/volumes.py, backend/app/collectors/dell/alerts.py |

### Dell/Oracle/Hitachi alerts

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Alert dedup uses Python hash() for message_id — randomized per process, so every restart re-inserts and re-notifies all alerts | backend/app/collectors/dell/alerts.py, backend/app/collectors/oracle/alerts.py,  |
| medium | bug | Empty native alert id collapses to message_id=0, merging unrelated alerts | backend/app/collectors/dell/alerts.py, backend/app/collectors/oracle/alerts.py |

### Detail modals (copy button)

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Clipboard copy shows success even when it fails and leaves an unhandled promise rejection | frontend/src/pages/Volumes.tsx, frontend/src/pages/Hosts.tsx |

### Frontend Capacity page — FleetGrowthSummary 'Avg Rate' + ForecastPanel rate line

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | bug | Negative (reclaiming) rate rendered without a minus sign | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — FleetGrowthSummary + FleetTrendChart

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | Two independent daily-trend range selectors desync and duplicate the fetch | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — FleetGrowthSummary / FleetTrendChart / projection

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Growth metrics conflate fleet-composition changes with organic growth | frontend/src/pages/Capacity.tsx, backend/app/services/capacity_projection.py |

### Frontend Capacity page — FleetGrowthSummary GrowthStat

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | 'Projected Full' stat shows a downward-trend icon for imminently-full arrays | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — FleetGrowthSummary Net Change

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | 'Net Change (Xd)' span is point-count, not calendar days, and mislabels gaps | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — FleetGrowthSummary Net Change tile

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | Flat/zero net change is styled as a decline (red, no sign) | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — ForecastPanel

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | bug | Clearing the forecast date renders 'Projected by Invalid Date' | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — VolumeGrowthSection volume dropdown

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | broken | Volume picker silently truncated to 500 volumes | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — all range-driven queries

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | Range switches blank the charts — no placeholder/previous data retained | frontend/src/pages/Capacity.tsx |

### Frontend Capacity page — linregSlope

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | Duplicated slope math + dead client-side fallback (linregSlope) | frontend/src/pages/Capacity.tsx, backend/app/services/capacity_projection.py |

### Frontend Capacity page — trendDate / FleetTrendChart / FleetGrowthSummary

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Daily-trend date labels shift one day earlier in western timezones (date-only string parsed as UTC) | frontend/src/pages/Capacity.tsx, backend/app/services/capacity_projection.py |

### Frontend Chat / backend selector

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Backend selector (local/dgx) is not persisted while messages are — reloads a DGX conversation as 'Local' | frontend/src/components/chat/ChatPanel.tsx |

### Frontend Chat / context building

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Multi-turn context pairs each question with the WRONG SQL (off-by-index over a filtered array) | frontend/src/api/chat.ts |

### Frontend Chat / error states

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | ux | All request failures show a hardcoded "Make sure Ollama is running" message, wrong for DGX / timeout / 503-busy | frontend/src/components/chat/ChatPanel.tsx |

### Frontend Chat / message rendering

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | bug | Metadata/timing row is gated on duration_ms truthiness, so rows count and 0ms results are hidden | frontend/src/components/chat/ChatPanel.tsx |

### Frontend Chat / persistence

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | loadMessages trusts sessionStorage JSON without shape validation | frontend/src/components/chat/ChatPanel.tsx |

### Frontend Chat / streaming & UX

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | ux | No cancel/stop control and no streaming during long blocking inference — input is fully frozen with only a spinner for up to ~2 minutes | frontend/src/components/chat/ChatPanel.tsx |

### Frontend Chat / streaming & lifecycle

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | No AbortController: in-flight request is not cancelled on unmount, navigation, or Clear chat — late response re-populates a cleared conversation | frontend/src/components/chat/ChatPanel.tsx |

### Frontend Chat / timeout handling

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Client timeout (120s) is shorter than backend per-LLM-call timeout (180s), so slow-but-valid answers surface as errors | frontend/src/api/chat.ts, backend/app/api/v1/chat.py |

### Frontend Dashboard / Active Alerts table

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | alert.opened is rendered as a raw backend timestamp string with no formatting |  |
| low | risk | alert.severity.toUpperCase() is called unconditionally and will throw if severity is null/absent |  |

### Frontend Dashboard / ArrayModal

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | broken | Drilldown Volumes/Hosts tables are hard-capped at 200 rows with no truncation indicator, while the tab count shows the true total |  |
| low | tech-debt | ArrayModal stores the tab as initial-only state and carries no key, so a changed initialTab/arrayName would show a stale tab if the modal ever stays mounted across selections |  |

### Frontend Dashboard / ArraysTableModal + ArrayModal

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | Modals lack Escape-to-close, body-scroll-lock, and focus trapping |  |

### Frontend Dashboard / DRY

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | Duplicated filter/sort/color logic between Dashboard and ArraysTableModal, and a redundant inline capacity-color threshold |  |

### Frontend Dashboard / StatCards

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Active-alerts 'N critical / N warning' sub-label is computed from only the 50 newest alerts, so it undercounts at fleet scale |  |
| low | inconsistency | avg_data_reduction defaults to 1 server-side, so the Data Reduction card shows '1.00:1' even when no array reports reduction |  |

### Frontend Dashboard / column headers

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | The same field (capacity_total_bytes) is labelled 'Total' in one view and 'Usable' in another |  |

### Frontend Dashboard / data fetching

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | fleet-stats and active-alerts queries have no error handling — an endpoint outage silently renders misleading fallback values ('all clear', '—') instead of an error |  |

### Frontend Dashboard / formatters

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | formatBytes renders null capacity as '0 B' instead of '—', misrepresenting missing data as a real zero |  |

### Frontend Dashboard / formatters + fleet-stats contract

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | Fleet capacity StatCards mislabel binary TiB as decimal TB, and formatTB rolls up to PB base-1000 while formatBytes uses base-1024 — the same fleet shows different numbers in different widgets |  |

### Frontend Dashboard / rendering at scale

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | Neither modal renders no virtualization/pagination; the dashboard also renders every array as DOM rows — re-rendered wholesale every 60s |  |

### Frontend Settings - Add Array modal

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | ux | Add-Array: array is created but user sees nothing when the auto-verify throws | frontend/src/pages/Settings.tsx |

### Frontend Settings - Add/Edit Array

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | Add-Array cloud label taxonomy disagrees with the badge/grouping logic and with Edit modal | frontend/src/pages/Settings.tsx |

### Frontend Settings - Arrays tab

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | ux | Array enable/disable toggle and delete fail silently with no feedback | frontend/src/pages/Settings.tsx |

### Frontend Settings - Arrays tab (CMS sync)

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Inventory sync uses blocking native alert(), no in-flight guard, and bypasses the API helper | frontend/src/pages/Settings.tsx, frontend/src/api/settings.ts |

### Frontend Settings - Arrays verify

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Verify call can hang the UI indefinitely with no timeout | frontend/src/pages/Settings.tsx, backend/app/api/v1/arrays.py |

### Frontend Settings - Collection tab

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Collection interval controls only affect Pure jobs; other 6 vendors are hardcoded out | frontend/src/pages/Settings.tsx, backend/app/collectors/scheduler.py |

### Frontend Settings - Edit Array modal

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Edit-Array save has no error handling and cannot clear existing fields | frontend/src/pages/Settings.tsx, backend/app/api/v1/arrays.py |

### Frontend Settings - Notifications/Collection

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | Notifications/Collection inputs seed once from settings and never re-sync; toasts never clear | frontend/src/pages/Settings.tsx |

### Frontend Settings - Scheduler tab

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Scheduler pause/resume state is inferred from next_run truthiness | frontend/src/pages/Settings.tsx |

### HPE client

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | HPE port selection depends on 'model' string; missing/empty model silently defaults to 3Par port 8080 | backend/app/collectors/hpe/client.py |

### HPE volumes

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inefficiency | HPE VLUN->host mapping is O(hosts x vluns) recomputed per host | backend/app/collectors/hpe/volumes.py |

### HPE/Hitachi/Dell/Oracle metrics

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Cross-vendor data inconsistencies: naive-local collected_at vs GETDATE() history, HPE data_reduction is a RAID ratio, HPE IOPS from cache-hit counts | backend/app/collectors/hpe/metrics.py, backend/app/collectors/hitachi/metrics.py |

### Hitachi metrics

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inefficiency | Hitachi metrics makes a redundant discarded storage-info request and bypasses the client wrapper | backend/app/collectors/hitachi/metrics.py |

### Hitachi volumes

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | inefficiency | Hitachi volumes collection is N+1 and blocking (~370s) — per-host-group WWN calls plus 180s/90s serial timeouts | backend/app/collectors/hitachi/volumes.py |
| medium | bug | Hitachi LDEV query is unpaginated with count=8192 while VSP caps ~500 per request — large arrays silently truncated | backend/app/collectors/hitachi/volumes.py, backend/app/collectors/hitachi/client |

### NetApp alerts / schema

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | messages.message_id is INT; NetApp EMS index can exceed 2^31 on long-lived clusters |  |

### NetApp alerts auto-resolve + scheduler alert cleanup

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Age-based auto-resolve and alert purge use TRY_CAST(opened AS DATETIME2) on timezone-bearing timestamps, which returns NULL — both silently no-op |  |

### NetApp alerts severity classification

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | NetApp EMS severity map classifies every 'error' event as a notifying 'warning' — Teams alert noise; also caps at 200 events/poll |  |

### NetApp client pagination

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | NetApp get_all() silently truncates inventory at 5000 records |  |

### NetApp metrics collector

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | NetApp metrics: dead efficiency code, unweighted data-reduction mean, and redundant duplicate cluster/nodes calls |  |

### NetApp volumes collector

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | NetApp host->volume links reference LUN names while volumes are keyed by SVM-qualified volume name, so cross-references don't resolve |  |

### Oracle volumes

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | Oracle volume collection is a deep unbounded N+1 (pools -> projects -> luns + filesystems), all sequential | backend/app/collectors/oracle/volumes.py |

### Oracle volumes/metrics

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | broken | Oracle collector: online-only pool filter + deeply nested traversal can yield 0 volumes/0 capacity, and never collects hosts | backend/app/collectors/oracle/volumes.py, backend/app/collectors/oracle/metrics. |

### Pure + NetApp alert save

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | A single bad alert row aborts the entire per-array alert batch (all-or-nothing transaction) |  |

### Pure alerts collector

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Pure alerts never get resolved — open-only fetch with no resolution path means stale 'active' alerts and unbounded messages growth |  |

### Pure client / collectors

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | Pure client has no host fallback and ignores configured cred_key — unreachable arrays and wrong credential lookups |  |

### Pure metrics collector (uptime)

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | Pure metrics computes uptime by LIKE-scanning the messages table twice per array per poll and miscounts reboots |  |

### SQLite cache

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | SQLite mirror is write-only dead weight — no reader calls fetch_metrics, but every collect pays the write + lock contention |  |
| medium | risk | SQLite single-writer contention under the 16-worker storm is silently swallowed |  |

### Scheduler / arrays cache

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Global arrays cache is read/written without a lock — thundering-herd DB reloads on cache miss |  |

### Scheduler / cleanup

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inefficiency | Nightly alert cleanup does a non-sargable full scan of messages |  |

### Scheduler / executors

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | load_arrays uses the default executor while all other offloads use the shared 16-worker pool, which also serves maintenance jobs |  |

### Scheduler / job status

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Job-status error reporting: crash path uses 'error', normal path uses 'errors'; exceptions dropped from last_errors |  |

### Shared table UI

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | tech-debt | SortHeader copy-pasted across four pages instead of a shared component | frontend/src/pages/Volumes.tsx, frontend/src/pages/Hosts.tsx, frontend/src/pages |

### StorageGrid metrics collector

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | StorageGrid metrics are persisted with vendor='netapp' despite the collector registering as vendor 'storagegrid' |  |

### Table row interaction

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Row-click affordance is inconsistent between Volumes and Hosts | frontend/src/pages/Volumes.tsx, frontend/src/pages/Hosts.tsx |

### Tailwind config / shared components

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Tailwind brand-400 / brand-300 shades are undefined — active-state colors silently render as no-ops |  |

### VolumeModal / HostModal

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | ux | Detail modals have no Escape-to-close, no focus trap, and no ARIA roles | frontend/src/pages/Volumes.tsx, frontend/src/pages/Hosts.tsx |

### VolumeModal mapped-hosts rendering

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Modal host string parsing assumes a single-colon 'group:host' format and drops extra segments | frontend/src/pages/Volumes.tsx |

### Volumes notes editing

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | NotesCell blur handler unconditionally re-saves on Escape and fires duplicate PATCH on Enter | frontend/src/pages/Volumes.tsx |
| low | risk | Notes mutation has no error handling — failures are silent | frontend/src/pages/Volumes.tsx |

### Volumes table/modal display

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Volume 'used'/'size' formatted without knowing thin-provisioning; used can exceed size and reduction column mixes fields | frontend/src/pages/Volumes.tsx |

### Volumes/Hosts list query

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | ux | Tables blank to 'Loading...' on every page/sort/filter change (no placeholderData) | frontend/src/pages/Volumes.tsx, frontend/src/pages/Hosts.tsx |

### alerts API

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Alerts endpoint offers no way to view suppressed and non-suppressed together |  |

### analytics

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | bug | Per-array history endpoint keeps the TOP-N-ORDER-BY-ASC bug that was fixed for fleet-history |  |

### api/*.ts

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | Several API-client methods are untyped (return any) and chat types live outside types.ts |  |

### api/chat.ts

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Chat multi-turn context pairs questions with the wrong SQL (filtered index used against unfiltered array) |  |

### api/client.ts / arrays.ts / Navbar.tsx

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | 401 interceptor is a no-op placeholder; export helper and refresh toast swallow/ignore failures |  |

### api/types.ts

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | Vendor type union omits vendors the backend actually emits (storagegrid, ibm, veritas) |  |

### backend chat / DGX egress

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Off-network DGX egress audit logs only the question, not that result rows are sent off-host |  |

### backend chat / execution

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | CHAT_QUERY_TIMEOUT is configured but never applied — arbitrary LLM SQL can run for the full 60s connection timeout |  |

### backend chat / extract_sql

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | extract_sql_from_response only strips paired <think> tags; orphan-tag cases (handled by _strip_think) leak reasoning into extracted SQL |  |

### backend chat / forecast branch

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Forecast intent detection misroutes ordinary questions to the projection engine |  |
| medium | bug | Target-date parser treats the month word 'may' (and march/august) as an explicit forecast date |  |

### backend chat / reasoning handling

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | Reasoning-model handling is hardcoded to the substring 'qwen3'; other reasoning models silently misbehave |  |

### backend chat / sql_safety

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | risk | SELECT ... INTO is a mutation that bypasses the SQL safety validator |  |
| high | bug | add_safety_limits produces invalid T-SQL for DISTINCT queries (TOP inserted before DISTINCT) |  |
| medium | risk | Table allowlist is not enforced for unqualified tables or comma-joined tables — cross-schema/DB reads slip through |  |
| medium | bug | add_safety_limits leaves the outer query of a CTE/UNION unbounded |  |
| low | tech-debt | JSON/parsing functions the prompt declares unsupported are not blocked by the validator |  |

### backend chat / timeouts

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | Chat pipeline can block for ~3x the client/nginx timeout, holding a worker + inflight slot doing dead work |  |

### capacity-alerts/performance

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | Capacity threshold check opens a new DB connection per array inside the loop |  |

### capacity_alerts

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Teams notification silently suppressed when a capacity alert re-opens after resolving | backend/app/services/capacity_alerts.py |
| medium | inefficiency | Pooled DB connection is held open across the blocking Teams HTTP POST, one connection per array | backend/app/services/capacity_alerts.py, backend/app/services/notification.py |
| medium | risk | Notification throttle mixes app-local datetime.now() with SQL Server GETDATE() | backend/app/services/capacity_alerts.py, backend/app/services/capacity_projectio |
| low | bug | Float thresholds collapse to the same synthetic message_id, causing alert collisions | backend/app/services/capacity_alerts.py |

### capacity_projection

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | bug | Growth projection assumes contiguous daily rows; gaps make days-to-full over-optimistic | backend/app/services/capacity_projection.py |

### config

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | risk | API has no authentication and ships an insecure default SECRET_KEY | backend/app/core/config.py, backend/app/api/v1/settings.py |
| medium | risk | chat_dgx egress path contradicts the 'no data leaves' claim; opt-in but unguarded once set | backend/app/core/config.py |

### cross-cutting/contract

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inconsistency | PaginatedResponse schema in common.py is dead and contradicts the real wire contract |  |
| medium | inconsistency | Vendor enum drift: frontend union omits active 'storagegrid' and lists inactive vendors |  |

### cross-cutting/data

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Row-mapping helpers default missing vendor to 'pure', contradicting the schema default of 'unknown' |  |

### cross-cutting/maintainability

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | Duplicated helper logic (_safe_json, _TIB, pagination shaping, StorageGrid remap) copy-pasted across modules |  |

### cross-cutting/observability

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | risk | Pervasive silent exception swallowing hides operational failures |  |

### cross-cutting/units

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Capacity figures labeled 'TB' are actually TiB (÷2^40) throughout the app |  |

### hosts API

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | inefficiency | /hosts/storage-report is an N+1 loop, not the "single JOIN" its docstring claims |  |
| high | bug | /hosts/storage-report prefix-LIKE over-matches and double-counts shared volumes |  |
| medium | risk | /hosts/storage-report accepts an unbounded servers list (amplifies the N+1) |  |
| low | tech-debt | Misleading undefined `Dict` type annotation in host_storage_report (not a crash, but dead/incorrect) |  |

### hosts/performance

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | host_storage_report runs one SQL query per server despite claiming a bulk fetch |  |

### inventory

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | bug | Inventory sync aborts entirely on a single row with NULL Vendor or NULL ArrayNameKey | backend/app/services/inventory.py |
| medium | inefficiency | Inventory upsert is per-array N+1 (connection + transaction per row) | backend/app/services/inventory.py |
| medium | bug | resolve_cred_key: case-sensitive StorageGrid match and Hitachi fallback that always returns a credential | backend/app/services/inventory.py |
| medium | inconsistency | Decommissioned arrays get two different monitoring_status values depending on code path | backend/app/services/inventory.py |
| low | risk | Duplicate ArrayNameKey rows in DimStorageFinance are silently last-wins | backend/app/services/inventory.py |
| low | tech-debt | 'Dispostition' misspelling is load-bearing and silently couples code to a typo'd DB column | backend/app/services/inventory.py |

### keepass

| Sev | Category | Finding | Files |
|---|---|---|---|
| high | risk | KeePass background refresh becomes a tight retry storm when KeePass is unreachable | backend/app/services/keepass.py |
| low | inconsistency | KeePass module docstring contradicts the actual cache constants | backend/app/services/keepass.py |
| low | tech-debt | KeePass daemon refresh thread is started as an import side-effect | backend/app/services/keepass.py |
| low | inefficiency | get_credentials refetches per-caller on a miss with no single-flight (thundering herd) | backend/app/services/keepass.py |

### main.tsx / Navbar.tsx

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | Global 60s refetchInterval on ALL queries + Navbar invalidateQueries() cause fleet-wide refetch bursts |  |

### notification

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | broken | TEAMS_SEVERITIES config is dead — severity_enabled() is never called; all severities notify | backend/app/services/notification.py, backend/app/collectors/pure/alerts.py |
| medium | risk | Teams POST has no retry/backoff; transient failures drop the alert until the next resend window | backend/app/services/notification.py, backend/app/services/capacity_alerts.py |

### ops/config

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | deps.py references a non-existent AUTH_REQUIRED setting; stale retention comment in scheduler |  |

### ops/migrations

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | tech-debt | Schema migrations are embedded in startup with an unconditional ALTER on every boot and no migration framework |  |

### ops/scaling

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | Single uvicorn worker couples API + scheduler + chat; the documented split is not actually deployed |  |

### ops/security

| Sev | Category | Finding | Files |
|---|---|---|---|
| critical | risk | No authentication on any endpoint, including mutating ones, exposed on host network |  |
| medium | risk | Insecure SECRET_KEY default, with two different placeholder strings across config and compose |  |
| low | risk | SQL connections use TrustServerCertificate=yes (no TLS validation) |  |

### settings API

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | inefficiency | /settings/database runs exact COUNT(*) without NOLOCK on multi-million-row tables and omits the largest table |  |
| low | risk | MAX(CAST(collected_at AS NVARCHAR)) on the string-typed messages column is a lexicographic max |  |

### tailwind.config.js / index.html

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | tech-debt | darkMode:'class' configured but no `dark` class is ever applied — dead config |  |

### utils/formatters.ts

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | inconsistency | Byte formatting is base-1024 while TB formatting is base-1000 — same capacity shows different numbers |  |
| low | inconsistency | Formatter null-handling is inconsistent (formatBytes returns '0 B', others return em-dash) |  |

### volumes/hosts APIs

| Sev | Category | Finding | Files |
|---|---|---|---|
| low | bug | Free-text search LIKE has no ESCAPE; wildcard characters in the term change results |  |

### volumes/hosts/alerts APIs

| Sev | Category | Finding | Files |
|---|---|---|---|
| medium | risk | WITH (NOLOCK) on cache tables that collectors mutate risks dirty/partial reads |  |
| medium | tech-debt | Fetch/paginate/sort/JSON logic is duplicated verbatim across three routers |  |
| low | inconsistency | COUNT and data queries run in separate connections; pagination total can disagree with the page |  |
| low | bug | `limit` query param has an upper bound but no lower bound → negative values reach SQL and 500 |  |
| low | inconsistency | Inconsistent response shaping: alerts returns Pydantic objects while volumes/hosts return dicts; only /hosts/groups sets response_model |  |
| low | inefficiency | Deep OFFSET pagination on cache tables with no max offset |  |
