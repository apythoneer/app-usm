# USM — Due Diligence & Hardening Assessment (2026-07-15)

> Scope: full read of `docs/`, `plans/`, git history, and the backend/frontend/collector
> code as it stands on `feature/capacity-overhaul`. Every finding below was verified
> against code — doc claims were treated as unproven until checked. Nothing was modified.

---

## 1. Progress: further along than the docs admit

`plans/USM_tracker.md` is the nominal source of truth and is **~3 months stale**
(last updated 2026-04-24). It materially understates the project:

| | Tracker (Apr) | Reality (Jul) |
|---|---|---|
| Vendors | 2 (Pure, NetApp) | **7** (+ Dell, Hitachi, HPE, Oracle, NetApp StorageGrid) |
| Arrays | 20 | **~85–87** |
| Volumes | 1,379 | **~45,683** |
| Version | v2 | **v3.0.0 shipped** (`aa59445`) |

**Listed as gaps but actually DONE:** BUG-01/02/04 (NetApp health mapping, EMS 24h
filter, alert lifecycle), IMP-10 capacity forecasting, GAP-11 docs (5 current docs in
`docs/`), Phase 5 SQLite cache, inventory/CMS integration, and **all 8 items** of the
v3.1 UI plan (sortable server-side tables, alerts overhaul, two-tier tagging,
Logs→Settings, all-vendor verify).

**Still genuinely open:** GAP-01→05 (component extraction, hooks, stores, types,
WebSocket) — GAP-01 has *regressed*: Settings.tsx 691→960 lines, Dashboard.tsx
472→701. GAP-06/07/08 (auth), GAP-09 (tests), GAP-10 (CI).

The June perf review's **P1 batching claim is TRUE and verified** — `get_fast_cursor()`
(`db/session.py:98`) and `batch_upsert()` (`:130`) exist and all 6 vendors are batched
with zero per-row loops remaining. Its own caveat still stands: the 450s→Xs improvement
was **never measured**.

The uncommitted NetApp `array_fqdn`/`mgmt_ip` work is sound and is very likely the real
fix for the review's "11 of 23 NetApp arrays failing authentication" — NetApp was the
only vendor still hardcoding `https://{array_name}/api`.

---

## 2. P0 — Fix before anything else

### P0-1. KeePass master password is committed to git
`docker/docker-compose.yml:112` → the `KEEPASS_PASSWORD` env var carried a hardcoded
literal as its `:-` fallback. `docker/keepass/keepass_app.py:25` → the same literal as
an `os.environ.get` default. *(The value is deliberately not reproduced in this
document — see the commits below to recover it for rotation.)*

In history since `aa59445` (v3.0.0, 2026-05-10). This is the master password to the
vault holding **every array credential plus SQL Server**. `.env.example` is clean — the
leak is purely the compose default.

**Fix:** rotate the KeePass master password first (it must be assumed compromised),
then remove both defaults so startup fails loudly without the env var, then purge
history (`git filter-repo`) and force-push. Rotation matters more than the purge.

### P0-2. Every endpoint is unauthenticated
`api/v1/router.py:11-18` includes all 8 routers with no `dependencies=[...]`.
`get_current_user` is defined at `api/deps.py:18` and **referenced nowhere else**.
There is no login endpoint (`deps.py:15` points `tokenUrl` at `/api/v1/auth/login`,
which does not exist), no User model, no users table. `core/security.py:19-41` is dead
code. `deps.py:14` cites an `AUTH_REQUIRED` setting that **does not exist** in
`config.py` — flipping it does nothing.

Unauthenticated **writes**: `PATCH /volumes/notes` (`volumes.py:93`),
`POST|PUT|DELETE /arrays/managed` (`arrays.py:211,235,282`),
`PUT /settings/notifications` (`settings.py:126`), scheduler pause/resume/reschedule
(`scheduler.py:44-86`). `/docs` and `/redoc` are open (`main.py:73-74`).

CORS is *not* `*` (`main.py:80` uses an explicit allowlist) — but CORS is not a security
control; any non-browser client bypasses it entirely.

**Severity depends on network exposure** — see §6.

### P0-3. Text-to-SQL safety layer is bypassable (empirically verified)
`services/chat.py:338` executes raw LLM output (`cursor.execute(sql)`).
Parameterization is impossible by design, so `validate_sql` is the *only* control.
Running the real validator against attack strings — **5/5 returned `OK`**:

| Attack | Result |
|---|---|
| `SELECT * FROM [master].[sys].[sql_logins]` | BYPASS |
| `SELECT * FROM syscomments` | BYPASS |
| `SELECT * FROM USM.metrics_current, master.sys.sql_logins` | BYPASS |
| `... CROSS APPLY sys.dm_exec_sessions` | BYPASS |
| `... IN (SELECT name FROM sysusers)` | BYPASS |

Root causes:
- `sql_safety.py:72` — `if schema and table.lower() not in _ALLOWED_TABLES`. The
  allowlist **only applies when a schema qualifier is present**. `FROM syscomments`
  skips it. It's a denylist wearing an allowlist's clothes.
- `sql_safety.py:61-64` — the regex can't match bracket-quoted identifiers, so
  `[master].[sys]` yields **no match** → `table_refs` empty → **passes vacuously**.
  Worst failure direction: malformed input looks safest.
- Comma-joins and `CROSS APPLY` are invisible to the regex.

Row cap also bypassable (`add_safety_limits`): a CTE puts `TOP 100` on the *inner*
query leaving the outer unbounded; a literal `/* TOP */` comment in the first 50 chars
disables limiting entirely.

Blast radius: keyword denylist blocks writes textually, so this is primarily
**arbitrary read of the entire StorMart database + system catalogs**.

**Fix:** the real fix is not a better regex — give chat its own SQL login with
`GRANT SELECT` on exactly the 10 allowed tables. Make the database the trust boundary.

Related: `api/v1/chat.py:25` accepts unbounded client-supplied `context` which
`chat.py:274-277` replays as prior **assistant** turns — an attacker forges the model's
own history to steer generation. Persist context server-side by session ID.

### P0-4. A transient API error deletes an array's entire volume inventory
The chain, verified end to end:
1. `pure/client.py:54-69` — `get()` returns `None` on any failure. Silent.
2. `pure/volumes.py:61-118` — every parse is guarded `if vols:`, so `collect()` returns
   `{"volumes": {}, "hosts": {}, "host_groups": {}, "protection_groups": {}}`.
3. `base.py:211` — `if not data: raise`. **A 4-key dict is truthy.** Guard never fires.
4. `session.py:138` — `batch_upsert(delete_missing=True)` default; `:183` computes
   `stale = existing - set(incoming)` → with `rows=[]` that's **every row** → bulk DELETE.

Only **Pure** (`pure/volumes.py:188`) and **NetApp** (`netapp/volumes.py:130`) are
exposed. Hitachi/HPE/Dell/Oracle all have the guard (`hitachi/volumes.py:150-152`).

**The irony:** commit `faa3551` (2026-05-11) fixed this exact bug class for those four
vendors after a real incident, and explicitly exempted the other two —
*"NetApp/Pure already safe (diff-based upsert pattern)"*. That belief was carried into
the June `batch_upsert` refactor. It is wrong, and NetApp is precisely the vendor with
11 arrays already failing auth.

**Fix (one-liner, highest value in this document):** copy the `hitachi/volumes.py:150-152`
guard into `pure/volumes.py:188` and `netapp/volumes.py:130`. Then tighten `base.py:211`
to reject dicts whose values are all empty, and fix `docs/COLLECTOR_CONTRACT.md:39`
("must return non-empty dict" is satisfied by a dict of empty dicts).

### P0-5. The SQLite dual-write *hides* P0-4 from the dashboard
`base.py:152` — `_write_to_cache()` **has** the guard the SQL Server path lacks
(`if not volumes and not hosts: return`).

So on an empty collect: **SQL Server is wiped, SQLite is preserved** — permanently
divergent. And the read path is split: `arrays.py:78-88` reads fleet stats from SQLite
first (`cache.py:243-245` counts volumes there), while the volumes list reads SQL Server.

**Consequence:** the dashboard headline keeps reporting ~45,683 volumes from stale
SQLite while the Volumes page reads 0 from the wiped table. The cache masks the data
loss on the exact surface an operator checks first. The June review's P3 "read path
consistency" is not a documentation gap — it's a correctness bug.

---

## 3. High

| # | Finding | Evidence |
|---|---|---|
| H1 | **Capacity feature is unreachable** — 947-line page, no route, no nav item, on *all 5 branches*. ~1 month of work (6+ commits, backend endpoints, Excel export, Teams capacity alerting) behind a 2-line wiring gap. `docs/CAPACITY_ANALYTICS.md:53-54` falsely claims both were added. | `pages/Capacity.tsx` imported by nothing; `App.tsx`, `Sidebar.tsx` |
| H2 | **`.gitignore` silently blocks the test suite.** `git check-ignore backend/tests/test_arrays.py` → **ignored**. Every pytest file written for GAP-09 will be invisible to git. `pytest` also absent from requirements. | `.gitignore:69` (`test_*.py`) |
| H3 | **Teams webhook secret served to anonymous callers.** A webhook URL *is* the credential. Also leaks `db_server`/`db_database`. | `settings.py:105`, `:98-99` |
| H4 | **Raw backend log served unauthenticated** — 5000 lines incl. connection strings, SQL errors, KeePass failures. `/settings/keepass-entries` enumerates vault structure. | `settings.py:192-214`, `:219` |
| H5 | **KeePass creds fetched over plaintext HTTP with no auth.** Every array/SQL password crosses the network in cleartext. | `config.py:43`, `keepass.py:48` |
| H6 | **`openpyxl` imported but never declared** → `/analytics/capacity-export.xlsx` 500s in the container. Lazy import means it fails only on click. Docs claim it was added. | `analytics.py:739`; `requirements.txt`; `docs/CAPACITY_ANALYTICS.md:86` |
| H7 | **No `isError` handling anywhere** — 0 occurrences across ~20 `useQuery` calls. An API 500 renders "No arrays found — check collectors are running…". **An outage is indistinguishable from an empty fleet**, and the UI misdirects the operator. Worst possible failure mode for a monitoring tool. | `Dashboard.tsx:637`, `Alerts.tsx:207` |
| H8 | **Alerts "Show resolved" toggle is inverted.** `resolved: showResolved \|\| undefined` → unchecked sends *no* filter (resolved alerts leak into the active view); checked shows *only* resolved. Makes Alerts disagree with Dashboard, which correctly sends `resolved: false`. | `Alerts.tsx:131`; backend `alerts.py:46` |
| H9 | **TLS verification disabled fleet-wide** (`verify=False` + `disable_warnings` on all 7 vendor clients). Array admin creds are MITM-able. Consistent → fixable centrally. Also `TrustServerCertificate=yes` (`session.py:38`). | all `*/client.py:~15,~45` |
| H10 | **Unauthenticated amplification DoS** — `servers: List[str]` unbounded, one SQL query per element. 50k elements = 50k round trips. | `hosts.py:128,131,152` |
| H11 | **Unbounded full-table reads** — `SELECT ... FROM volumes_cache` with no TOP/WHERE, loaded into Python, then sliced `out[:limit]`. The cap bounds the *response*, not the *query*. | `analytics.py:575-583,607`; also `:371-385`, `:743` |
| H12 | **No error boundaries** — any render throw white-screens the app. | 0 matches for `ErrorBoundary` |

---

## 4. Medium

- **Scheduler `misfire_grace_time` never set** → APScheduler default is **1 second**; jobs firing >1s late are silently discarded. Plausible unmonitored source of skipped collections. (`scheduler.py:202`)
- **One shared 5-worker pool** serves all collectors *and* all 6 maintenance jobs — long volume cycles starve `daily_stats`/`inventory_sync`/`capacity_alerts`. Comment at `:26-27` says "10 concurrent"; code says `max_workers=5` (`:28`).
- **StorageGrid silent data loss** — `scheduler.py:70-71` remaps NetApp arrays whose model contains "StorageGrid" to `vendor="storagegrid"`, which has **only a metrics collector**. Those arrays silently stop getting volume and alert collection, with no warning logged.
- **No retry/backoff in any client** — one transient blip = whole-array failure, and for Pure/NetApp that now means P0-4.
- **No global exception handler** — `core/exceptions.py` defines per-class `status_code` but no handler is registered; `CredentialError` (intended 503) surfaces as a generic 500. The attribute is decorative. *(Stack traces are not leaked — `debug=True` is never passed — but that's accidental, not designed.)*
- **DB/SQL error text returned to clients** (`chat.py:262-263`, `:241`; `settings.py:185`) — turns P0-3 into a convenient blind-SQL oracle.
- **Unbounded log growth** — plain `FileHandler`, no rotation (`main.py:28`, `base.py:85`), and no Docker `max-size`. At 87 arrays this is a disk-fill outage.
- **`SECRET_KEY` defaults to `CHANGE_ME_BEFORE_PRODUCTION`** (`config.py:31`, `docker-compose.yml:42`) — harmless only because no auth exists; instant token forgery the day auth ships. Make it `Field(...)` with no default.
- **`batch_upsert` docstring lies** — `extra_where` is documented as applying to the DELETE (`session.py:162`) but is only applied to the SELECT (`:175`, not `:186`). Low impact today; latent trap for the next caller.
- **`last_errors` key mismatch** — crash path writes `{"error": ...}` (`scheduler.py:128`), reader expects `"errors"` (`:161`). Collector crashes show `arrays_failed: N` with an empty error list — undermines the planned health panel.
- **`docs/` and `mcp/` are entirely untracked** — 5 current architecture docs + 3 MCP servers exist only on this machine. One `rm -rf` from gone.
- **No CI, no backend tests, no frontend lockfile** — all-caret ranges + no `package-lock.json` = non-reproducible Docker builds with zero tests to catch drift.
- **Settings.tsx (960 lines) is effectively untyped** — 11 of the codebase's 14 `any`s, as whole component props (`settings: any`, `scheduler: any`). It's the page that writes config and mutates managed arrays. `tsconfig` is otherwise `strict: true` and the rest of the app is well-typed.
- **`limit` params have `le=` but no `ge=`** → `limit=-1` → `FETCH NEXT -1 ROWS` → 500.
- **Stale/incorrect docs** — `docker-compose.v2.yml` referenced in 4 docs but the file is `docker-compose.yml`; `keepass.py:8-10` docstring says TTL 3600s/30min, code says 86400/14400.
- **Junk file** — an empty file literally named `85]` in the repo root, from a shell mishap.

---

## 5. Verified as NOT problems (skepticism paid off)

- **CORS does not allow `*`** — explicit allowlist (`main.py:80`).
- **Credentials are never logged** — clients log only the *key name* (`netapp/client.py:49`, `pure/client.py:33`).
- **`ORDER BY` is not injectable** — all three list endpoints allowlist sort columns against a frozen set (`volumes.py:24-27`, `hosts.py:20-22`, `alerts.py:17-20`). Correctly done.
- **All non-chat SQL is parameterized** with `?` placeholders. The f-strings are `{SCHEMA}` from config, not user input.
- **No unbounded 45k-volume fetch to the browser** — Volumes/Hosts/Alerts are server-paginated with server-side sort and debounced search; fleet aggregates come from a dedicated endpoint. Dashboard steady-state ≈ 5 req/min.
- **No XSS** — zero `dangerouslySetInnerHTML`; chat renders LLM output as React-escaped text children. LLM SQL is displayed, never executed client-side.
- **Single-array failure does not kill a cycle** — three independent catch layers (`base.py:228`, `scheduler.py:126`, `:148` `return_exceptions=True`).
- **No missing HTTP timeouts** — every client passes explicit `timeout=`. Values are inconsistent (10/15/30/60s) but nothing is unbounded.
- **Job overlap protection exists** — `max_instances=1, coalesce=True` on every job.
- **Collector vendor parity is real** — all 18 registrations present, no stubs, no `NotImplementedError`, no TODOs.
- **The P1 batching fix is genuinely shipped** — verified per-vendor.

---

## 6. The one decision that reorders everything

**Is `:8000` reachable beyond the sandbox host, and who can reach it?**

- **If it's genuinely isolated** (host-only / tight subnet, trusted operators): P0-2 and
  P0-3 drop to "important, schedule it." The ordering becomes **P0-4 → P0-5 → P0-1 →
  H1/H2 → auth**. Data loss beats theoretical access.
- **If any corp-network host can curl it:** P0-1/2/3 are an active incident. An
  unauthenticated `/api/v1/chat` that reads arbitrary SQL Server tables, plus a webhook
  secret and raw logs on open endpoints, is a breach waiting to be discovered. Rotate the
  KeePass password *today* regardless.

The KeePass rotation (P0-1) is worth doing immediately under either answer — the password
is in git history, and that exposure is independent of network position.

---

## 7. Recommended sequence

**Week 1 — stop the bleeding (small, high-value, low-risk):**
1. Rotate the KeePass master password; remove both hardcoded defaults. *(P0-1)*
2. Add the empty-collect guard to `pure/volumes.py` + `netapp/volumes.py`. *(P0-4 — one-liner)*
3. Tighten `base.py:211` + fix `COLLECTOR_CONTRACT.md:39`. *(defends all vendors)*
4. Fix `.gitignore:69` — unblock tests before writing any. *(H2)*
5. Add `openpyxl` to requirements. *(H6)*
6. Wire the `/capacity` route + nav item — unlock a month of finished work. *(H1)*
7. Fix the Alerts `resolved` toggle. *(H8)*
8. Commit `docs/` and `mcp/`; delete `85]`. *(unversioned work)*

**Week 2 — make failure visible:**
9. `isError` handling + an ErrorBoundary. *(H7, H12 — a monitoring tool must not report outages as empty state)*
10. Resolve the SQLite/SQL Server read-path divergence. *(P0-5)*
11. Log rotation + Docker `max-size`.
12. Global exception handler; stop returning DB error text.
13. `misfire_grace_time`, split the executor, fix `last_errors`.

**Week 3+ — the real hardening:**
14. Chat gets a read-only DB login scoped to 10 tables; stop trusting client `context`. *(P0-3 — fix the trust boundary, not the regex)*
15. Auth: user store + `/auth/login` + `dependencies=[Depends(get_current_user)]`; `SECRET_KEY` mandatory. *(P0-2)*
16. Close H3/H4/H5 (webhook secret, logs endpoint, KeePass over authenticated HTTPS).
17. Pytest + CI; frontend lockfile. Nothing above stays fixed without this.
18. Then GAP-01 component extraction — with tests to refactor against.

**Also worth doing:** measure the P1 volume-cycle improvement (still unverified after a
month), and rewrite `USM_tracker.md` — a source of truth that's 3 months and 5 vendors
stale is actively misleading whoever reads it next.
