"""
Chat Service — orchestrates natural language → SQL → answer pipeline.
Uses a local Ollama LLM (no data leaves the host).
"""

import json
import logging
import re
import time
import requests
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.db.session import get_db_cursor, rows_to_dicts
from app.services.sql_safety import validate_sql, extract_sql_from_response, add_safety_limits
from app.services.capacity_projection import forecast_capacity

logger = logging.getLogger("usm.chat")
settings = get_settings()
SCHEMA = settings.db_schema

# ── Schema context for the LLM ─────────────────────────────────────────────

_SCHEMA_CORE = f"""
DATABASE: SQL Server — database StorMart, schema {SCHEMA}

TABLE {SCHEMA}.metrics_current — Latest metrics snapshot per storage array (one row per array, updated every 60s)
  Columns: array_name NVARCHAR(255) [PK-like, unique], vendor NVARCHAR(50) ['pure'|'netapp'],
  purity_version NVARCHAR(100), read_iops INT, write_iops INT,
  read_latency_us INT (microseconds), write_latency_us INT (microseconds),
  read_bandwidth BIGINT (bytes/sec), write_bandwidth BIGINT (bytes/sec),
  capacity_total BIGINT (bytes), capacity_used BIGINT (bytes), capacity_used_pct FLOAT,
  data_reduction FLOAT, total_reduction FLOAT,
  shared_space BIGINT (bytes), snapshot_space BIGINT (bytes), volume_space BIGINT (bytes),
  array_status NVARCHAR(50), controller_status NVARCHAR(50),
  uptime_seconds BIGINT, uptime_str NVARCHAR(100), last_reboot NVARCHAR(50), reboot_count INT,
  network_status NVARCHAR(50), collected_at NVARCHAR(50)

TABLE {SCHEMA}.volumes_cache — Volume inventory (refreshed every 15min)
  Columns: array_name, vendor, volume_name, size BIGINT (bytes), used BIGINT (bytes),
  data_reduction FLOAT, total_reduction FLOAT, snapshots INT, created NVARCHAR(50),
  serial NVARCHAR(100), hosts NVARCHAR(MAX) [JSON array of host names],
  host_groups NVARCHAR(MAX) [JSON array], protection_groups NVARCHAR(MAX) [JSON array],
  notes NVARCHAR(MAX)

TABLE {SCHEMA}.hosts_cache — Host/initiator inventory
  Columns: array_name, vendor, host_name, iqn NVARCHAR(500), wwn NVARCHAR(500),
  nqn NVARCHAR(500), host_group NVARCHAR(255), volumes NVARCHAR(MAX) [JSON array of volume names]

TABLE {SCHEMA}.host_groups_cache — Host group inventory
  Columns: array_name, vendor, hgroup_name, hosts NVARCHAR(MAX) [JSON], volumes NVARCHAR(MAX) [JSON]

TABLE {SCHEMA}.protection_groups_cache — Replication/protection group inventory
  Columns: array_name, vendor, pgroup_name, volumes NVARCHAR(MAX) [JSON],
  hosts NVARCHAR(MAX) [JSON], targets NVARCHAR(MAX) [JSON], replication_enabled BIT

TABLE {SCHEMA}.messages — Alert/event history
  Columns: array_name, vendor, message_id INT, event NVARCHAR(500), severity NVARCHAR(50),
  component_type NVARCHAR(100), component_name NVARCHAR(255),
  opened NVARCHAR(50), closed NVARCHAR(50), teams_notified DATETIME2,
  snow_ticket NVARCHAR(100), suppressed BIT, resolved BIT

TABLE {SCHEMA}.managed_arrays — Array inventory + lifecycle, synced nightly from the CMS/DimStorageFinance source of record. USE THIS for model/site/support/EOSL questions ("which arrays are past EOSL", "arrays by site", "what goes out of support this year").
  Columns: array_name NVARCHAR(255) [unique], vendor, group_label NVARCHAR(100) [cloud/site tag],
  enabled BIT [0 = excluded from collection], monitoring_status NVARCHAR(50),
  model NVARCHAR, site NVARCHAR, technology NVARCHAR, category NVARCHAR, usage_label NVARCHAR,
  disposition NVARCHAR ['Current' = active asset], oem NVARCHAR, support_provider NVARCHAR,
  array_serial NVARCHAR, array_fqdn NVARCHAR, mgmt_ip NVARCHAR,
  install_date DATE, eosl_date DATE [end of service life], maint_end_date DATE,
  dim_sync_at DATETIME2
  NOTE: cred_key exists on this table but is a credential-vault reference — never
  select it, never mention it.

TABLE {SCHEMA}.daily_stats — Aggregated daily fleet summary
  Columns: stat_date DATE [unique], total_arrays INT, total_volumes INT, total_hosts INT,
  total_capacity_tb FLOAT, total_used_tb FLOAT, avg_utilization_pct FLOAT,
  avg_data_reduction FLOAT, critical_alerts INT, warning_alerts INT, info_alerts INT

TABLE {SCHEMA}.metrics_history — Time-series performance data (USE THIS for any historical/time-based queries like "last 24 hours", "last 7 days", "trends", "peak", "highest ever")
  The collected_at column is DATETIME2 — safe for date math with DATEADD/DATEDIFF.
  Columns: array_name, vendor, collected_at DATETIME2, read_latency_us FLOAT,
  write_latency_us FLOAT, read_iops FLOAT, write_iops FLOAT, capacity_total BIGINT,
  capacity_used BIGINT, capacity_used_pct FLOAT, data_reduction FLOAT
  NOTE: metrics_current.collected_at is NVARCHAR — do NOT use DATEADD on it. For time filters, always use metrics_history.

TABLE {SCHEMA}.volumes_history — Per-volume time-series, one row per volume per collection (every 30 min). USE THIS for volume growth/shrink over time, "which volumes grew", per-volume trends.
  Columns: array_name, vendor, volume_name, collected_at DATETIME2,
  size BIGINT (bytes), used BIGINT (bytes), data_reduction FLOAT, total_reduction FLOAT,
  snapshots BIGINT (snapshot SPACE in bytes, NOT a count)
  NOTE: very large table (40M+ rows). ALWAYS filter by collected_at and/or array_name/volume_name.

-- ── COMMON QUERY PATTERNS (follow these table choices exactly) ───────────────
-- Array names look like '2bja', '09rw', 'purecbs-gp-prod-eus2-02' — they are
-- exact array_name values. Match with `=`, NOT LIKE.
--
-- "what hosts are on / connected to array X"
--     SELECT host_name, wwn, iqn, host_group FROM {SCHEMA}.hosts_cache WHERE array_name = 'X'
--     (Use hosts_cache. Do NOT read the volumes_cache.hosts JSON column for this.)
-- "what volumes are on array X"
--     SELECT volume_name, size, used FROM {SCHEMA}.volumes_cache WHERE array_name = 'X'
-- "how full is array X" / "utilization of X"
--     SELECT array_name, capacity_used_pct, capacity_total, capacity_used FROM {SCHEMA}.metrics_current WHERE array_name = 'X'
-- "arrays over N% utilized"
--     SELECT array_name, vendor, capacity_used_pct FROM {SCHEMA}.metrics_current WHERE capacity_used_pct > N ORDER BY capacity_used_pct DESC
-- "open/active alerts [on X]"
--     SELECT array_name, severity, event, opened FROM {SCHEMA}.messages WHERE resolved = 0 AND suppressed = 0 [AND array_name = 'X']
""".strip()


# ── CMS block: appended ONLY for questions that need it ───────────────────────
#
# Measured on the live host: adding this block took the system prompt from ~7.2k
# to ~14.6k chars and _generate_sql on the CPU-hosted qwen2.5:3b from usable to
# 78.7s — with the full pipeline (generate -> execute -> format, plus a _fix_sql
# retry) blowing past the 120s nginx/UI timeout. The model also began
# hallucinating ACROSS tables (selecting the CMS column app_acronym FROM
# volumes_cache), i.e. more schema made it less correct, not more capable.
#
# So the CMS schema is opt-in per question. Storage questions keep the small,
# fast prompt; app/server/database questions pay for the bigger one.
_SCHEMA_CMS = f"""
-- ── CMS / CMDB asset model (read-only views over dbo.CMS*) ──────────────────
-- These answer "what runs on this storage?". Join key to USM: array_name.
-- Filters (In Use / Production) and all joins are ALREADY applied inside the
-- views — do NOT add ASSIGNMENT filters or join CMS base tables yourself.

VIEW {SCHEMA}.vw_cms_array_to_app_db — THE bridge from storage to applications. array_name matches {SCHEMA}.metrics_current.array_name and {SCHEMA}.volumes_cache.array_name. Use this to answer "which apps/databases are on array X".
  Columns: array_name, vendor, datacenter, host_group, host_name, cms_host_name, host_ip,
  allocated_gb FLOAT, used_gb FLOAT, app_id, app_acronym, app_name,
  app_sox_critical ['Yes'|'No'|NULL], app_bia_critical ['Yes'|'No'|NULL],
  database_name, database_type, database_status
  VALUES: the criticality flags are the strings 'Yes'/'No' — NOT 'Y'/'N', NOT 1/0.
  NULL app_acronym/app_name means the host has storage but no mapped application
  (~9k rows). For "which applications..." questions you MUST add
  `WHERE app_acronym IS NOT NULL` or the results are mostly empty app rows.

VIEW {SCHEMA}.vw_cms_app_to_server — which servers an application runs on
  Columns: app_id, app_acronym, app_name, app_sox_critical ['Yes'|'No'],
  app_bia_critical ['Yes'|'No'], app_criticality ['Critical'|'Non-Critical'],
  server_name, server_os, server_os_family, server_status, server_model, server_physical_virtual, server_ip

VIEW {SCHEMA}.vw_cms_app_to_database — which databases an application owns
  Columns: app_id, app_acronym, app_name, app_sox_critical, database_assettag,
  database_name, database_type, database_version, database_instance, database_status

VIEW {SCHEMA}.vw_cms_database_to_server — which server hosts a database
  Columns: database_assettag, database_name, database_type, database_status,
  server_name, server_os, server_status, server_ip

VIEW {SCHEMA}.vw_cms_app_to_database_to_server — full app → database → server chain
  Columns: app_id, app_acronym, app_name, app_sox_critical, database_assettag,
  database_name, database_type, server_name, server_os, server_status

VIEW {SCHEMA}.vw_cms_server_to_cluster — server → cluster membership
  Columns: server_name, server_status, cluster_name, cluster_status, cluster_model, cluster_brand

VIEW {SCHEMA}.vw_cms_vm_to_host — virtual machine → physical ESX host
  Columns: vm_name, esx_host_name, cluster_name

VIEW {SCHEMA}.vw_cms_server_to_backups — server → backup records
  Columns: server_name, server_status, backup_node, backup_name, backup_date DATETIME, backup_exemption

VIEW {SCHEMA}.vw_cms_switch_to_host_app_db — SAN switch/port → host → array → app/db
  Columns: switch_name, fabric, slot, port, hba_wwpn, port_wwpn, host_name,
  array_name, vendor, app_acronym, app_name, database_name
  NOTE: one row per switch-port x app/db combination, so it fans out (a host with
  8 ports and 120 apps yields 960 rows). Use DISTINCT or aggregate, and always
  filter by switch_name or host_name.

-- ── CMS QUERY PATTERNS ──────────────────────────────────────────────────────
-- "what applications are on array X"
--     SELECT DISTINCT app_acronym, app_name FROM {SCHEMA}.vw_cms_array_to_app_db WHERE array_name = 'X' AND app_acronym IS NOT NULL
-- "what databases are on array X"
--     SELECT DISTINCT database_name, database_type FROM {SCHEMA}.vw_cms_array_to_app_db WHERE array_name = 'X' AND database_name IS NOT NULL
-- "what servers does app X run on"
--     SELECT server_name, server_os, server_status FROM {SCHEMA}.vw_cms_app_to_server WHERE app_acronym = 'X'
""".strip()

# Full schema — kept for callers/tests that want the complete picture.
_SCHEMA_CONTEXT = _SCHEMA_CORE + "\n\n" + _SCHEMA_CMS

# Words that mean "this question needs the CMS/CMDB tables".
#
# Deliberately NOT including "host" or "hosts": USM has its OWN hosts_cache
# (storage initiators), so "how many hosts on array X" is a core question. Adding
# "host" here would drag the CMS block into most storage questions and reintroduce
# the very slowdown this selector exists to avoid.
_CMS_KEYWORDS = (
    "app", "application", "server", "database", "db", "oracle", "sql server",
    "cluster", "vm", "virtual machine", "esx", "switch", "fabric", "wwpn",
    "backup", "sox", "bia", "critical", "cmdb", "cms", "owner", "business",
)


def _needs_cms(question: str) -> bool:
    """True if the question looks like it needs the CMS/CMDB schema block."""
    q = (question or "").lower()
    return any(k in q for k in _CMS_KEYWORDS)


def build_system_prompt(question: str) -> str:
    """System prompt with only as much schema as the question requires.

    Returns the ~7.2k-char core prompt for storage questions, or the ~14.6k-char
    full prompt when the question mentions apps/servers/databases. On the
    CPU-hosted 3b model that difference is roughly 78.7s vs a usable response.
    """
    schema = _SCHEMA_CONTEXT if _needs_cms(question) else _SCHEMA_CORE
    # NB: _SYSTEM_PROMPT_TMPL is an f-string, so the `{{SCHEMA_BLOCK}}` written in
    # the source has ALREADY collapsed to a literal `{SCHEMA_BLOCK}` by the time
    # we see it. Matching the double-brace form silently replaces nothing and
    # ships a prompt with NO schema at all — which is exactly what happened, and
    # only surfaced because the test asserted the CMS block was present rather
    # than trusting that replace() had done something.
    out = _SYSTEM_PROMPT_TMPL.replace("{SCHEMA_BLOCK}", schema)
    if "{SCHEMA_BLOCK}" in out:  # placeholder survived => substitution failed
        raise RuntimeError("system prompt placeholder was not substituted")
    return out

_SYSTEM_PROMPT_TMPL = f"""You are a storage infrastructure analyst assistant for the Unified Storage Monitoring (USM) platform.
You help answer questions about storage arrays, volumes, hosts, alerts, and capacity by writing SQL Server (T-SQL) queries.

{{SCHEMA_BLOCK}}

IMPORTANT RULES:
1. Write ONLY a single SELECT query — never write INSERT, UPDATE, DELETE, DROP, or any mutation.
2. Always qualify tables with the {SCHEMA} schema prefix (e.g., {SCHEMA}.metrics_current).
3. Use TOP 100 to limit result sets.
4. Convert bytes to TB: divide by 1099511627776.0 and ROUND to 2 decimals.
5. Convert bytes to GB: divide by 1073741824.0.
6. Latency is in microseconds (us). Convert to ms by dividing by 1000.0 when displaying.
7. For host/volume counts on an array, use the volumes_cache and hosts_cache tables.
8. The hosts column in volumes_cache is a JSON array string — use it for impact analysis.
9. For "active" or "open" alerts, filter: resolved = 0 AND suppressed = 0.
10. Return ONLY the SQL query with no explanation. No markdown code fences.

CRITICAL SQL RULES:
- Do NOT use STRING_SPLIT, JSON_VALUE, OPENJSON, or any JSON parsing functions — this SQL Server does not support them.
- Do NOT try to parse the hosts, volumes, or host_groups NVARCHAR(MAX) columns — they store JSON strings but cannot be queried with SQL functions.
- When user mentions a partial array/host name like "eus2-02" or "awuse2h", use LIKE '%partial_name%' to match.
- For capacity impact as percentage: use a subquery with NO WHERE clause to get total fleet capacity.
- The metrics_current table has capacity_total and capacity_used (in bytes). Use THESE for array-level capacity questions.
- The volumes_cache table has size and used (in bytes) per volume. Use this for volume-level detail.
- The hosts_cache table has ONE ROW PER HOST per array. To find which array a host is on, just query hosts_cache WHERE host_name LIKE '%name%'.
- Do NOT join hosts_cache to volumes_cache — they are independent tables keyed by array_name.
- To count hosts for an array: SELECT COUNT(*) FROM {SCHEMA}.hosts_cache WHERE array_name = 'X'
- To find which array a host connects to: SELECT array_name, host_name, host_group FROM {SCHEMA}.hosts_cache WHERE host_name LIKE '%X%'
- To look up volume details: SELECT array_name, volume_name, size, used, data_reduction, serial, created FROM {SCHEMA}.volumes_cache WHERE volume_name LIKE '%X%'
- The volumes_cache "hosts" column is NVARCHAR(MAX) JSON — do NOT use it in WHERE or JOIN clauses.
- The hosts_cache "volumes" column is NVARCHAR(MAX) JSON — do NOT use it in WHERE or JOIN clauses.
- To find which array a volume is on: query volumes_cache WHERE volume_name LIKE '%name%' — the array_name column tells you which array.

EXAMPLES:
Q: How much total capacity do we have?
A: SELECT COUNT(*) AS total_arrays, ROUND(SUM(capacity_total) / 1099511627776.0, 2) AS total_tb, ROUND(SUM(capacity_used) / 1099511627776.0, 2) AS used_tb, ROUND(AVG(capacity_used_pct), 1) AS avg_util_pct FROM {SCHEMA}.metrics_current

Q: Which arrays are over 80% utilized?
A: SELECT TOP 100 array_name, vendor, capacity_used_pct, ROUND(capacity_total / 1099511627776.0, 2) AS total_tb FROM {SCHEMA}.metrics_current WHERE capacity_used_pct > 80 ORDER BY capacity_used_pct DESC

Q: How many hosts would be affected if purecbs-gp-prod-eus2-02 goes down?
A: SELECT COUNT(*) AS host_count FROM {SCHEMA}.hosts_cache WHERE array_name = 'purecbs-gp-prod-eus2-02'

Q: If eus2-02 goes down, what percentage of total capacity is affected?
A: SELECT ROUND(a.capacity_total / 1099511627776.0, 2) AS array_tb, ROUND(t.fleet_total / 1099511627776.0, 2) AS fleet_total_tb, ROUND(a.capacity_total * 100.0 / t.fleet_total, 2) AS pct_of_fleet FROM {SCHEMA}.metrics_current a CROSS JOIN (SELECT SUM(capacity_total) AS fleet_total FROM {SCHEMA}.metrics_current) t WHERE a.array_name LIKE '%eus2-02%'

CRITICAL: In the percentage query above, the subquery "(SELECT SUM(capacity_total) AS fleet_total FROM {SCHEMA}.metrics_current)" has NO WHERE clause. It sums ALL arrays. Only the OUTER query has WHERE to filter the specific array. NEVER put a WHERE clause inside the fleet_total subquery.

Q: Which array is host awuse2hcan05 connected to?
A: SELECT TOP 100 array_name, host_name, host_group FROM {SCHEMA}.hosts_cache WHERE host_name LIKE '%awuse2hcan05%'

Q: Give me details about host awuse2hcan05
A: SELECT TOP 100 array_name, host_name, host_group, iqn, wwn FROM {SCHEMA}.hosts_cache WHERE host_name LIKE '%awuse2hcan05%'

Q: What is the affected storage capacity if an array goes down?
A: SELECT array_name, ROUND(capacity_total / 1099511627776.0, 2) AS capacity_tb, ROUND(capacity_used / 1099511627776.0, 2) AS used_tb, capacity_used_pct FROM {SCHEMA}.metrics_current WHERE array_name LIKE '%{{array_name}}%'

Q: Show capacity breakdown by vendor
A: SELECT vendor, COUNT(*) AS arrays, ROUND(SUM(capacity_total) / 1099511627776.0, 2) AS total_tb, ROUND(SUM(capacity_used) / 1099511627776.0, 2) AS used_tb, ROUND(AVG(capacity_used_pct), 1) AS avg_util_pct FROM {SCHEMA}.metrics_current GROUP BY vendor

Q: Which array had the highest IOPS in the last 24 hours?
A: SELECT TOP 1 array_name, MAX(read_iops + write_iops) AS peak_total_iops, MAX(read_iops) AS peak_read_iops, MAX(write_iops) AS peak_write_iops FROM {SCHEMA}.metrics_history WHERE collected_at >= DATEADD(hour, -24, GETDATE()) GROUP BY array_name ORDER BY peak_total_iops DESC

Q: Which array had the highest IOPS in the last 7 days?
A: SELECT TOP 5 array_name, MAX(read_iops + write_iops) AS peak_total_iops, ROUND(AVG(read_iops + write_iops), 0) AS avg_total_iops FROM {SCHEMA}.metrics_history WHERE collected_at >= DATEADD(day, -7, GETDATE()) GROUP BY array_name ORDER BY peak_total_iops DESC

Q: Show me latency trends for array X in the last 24 hours
A: SELECT TOP 100 array_name, collected_at, ROUND(read_latency_us / 1000.0, 2) AS read_latency_ms, ROUND(write_latency_us / 1000.0, 2) AS write_latency_ms FROM {SCHEMA}.metrics_history WHERE array_name LIKE '%X%' AND collected_at >= DATEADD(hour, -24, GETDATE()) ORDER BY collected_at DESC

Q: Get details for volume nadnp1a_cifs_svm:dnp1a_vol13
A: SELECT TOP 100 array_name, volume_name, ROUND(size / 1073741824.0, 2) AS size_gb, ROUND(used / 1073741824.0, 2) AS used_gb, data_reduction, serial, created FROM {SCHEMA}.volumes_cache WHERE volume_name LIKE '%dnp1a_vol13%'

Q: Which array has volume X?
A: SELECT array_name, volume_name, vendor FROM {SCHEMA}.volumes_cache WHERE volume_name LIKE '%X%'

Q: Which volumes does host awuse2oraccsp1 have?
A: SELECT v.array_name, v.volume_name, ROUND(v.size / 1073741824.0, 2) AS size_gb FROM {SCHEMA}.volumes_cache v WHERE v.array_name IN (SELECT array_name FROM {SCHEMA}.hosts_cache WHERE host_name LIKE '%awuse2oraccsp1%')

Q: Which hosts are connected to volume dnp1a_vol09?
A: SELECT array_name, volume_name, hosts FROM {SCHEMA}.volumes_cache WHERE volume_name LIKE '%dnp1a_vol09%'

NOTE: The "hosts" column in volumes_cache contains a JSON string listing connected hosts (e.g. '["host1","host2"]'). For "which hosts on this volume" queries, simply SELECT the hosts column — do NOT try to parse it with JSON functions. Just return it as-is.

IMPORTANT: For any question about "last N hours/days", "historical", "peak", "trends", "over time" — ALWAYS use {SCHEMA}.metrics_history (DATETIME2 collected_at). NEVER use metrics_current for time-based filtering.
"""

_FORMAT_PROMPT = """You are a helpful storage infrastructure assistant. Given the SQL query results below,
provide a clear, concise natural language answer to the user's question.
Keep your answer to 2-4 sentences. Use exact numbers from the data.
Format large numbers with commas. Use TB for terabytes, GB for gigabytes.

User question: {question}
SQL query: {sql}
Results ({row_count} rows):
{results}

Answer:"""


import re as _re

_THINK_RE = _re.compile(r"<think>.*?</think>", _re.DOTALL | _re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Strip qwen3 chain-of-thought, including the malformed cases seen live.

    qwen3 emits reasoning before the answer. In practice the tags are often
    NOT a clean <think>...</think> pair:
      - orphan close: "Okay, let's see...</think>The answer"  (no opening tag)
      - orphan open (truncated mid-thought): "<think>Okay, let's..."  (no close)
    The first case is exactly what leaked verbatim into the DGX answers, because a
    pair-only regex matched nothing. Handle all three shapes.
    """
    t = text or ""
    t = _THINK_RE.sub("", t)                       # 1. complete <think>...</think>
    if "</think>" in t:                            # 2. orphan close -> keep tail
        t = t.rsplit("</think>", 1)[-1]
    t = _re.sub(r"<think>.*$", "", t, flags=_re.DOTALL | _re.IGNORECASE)  # 3. orphan open
    return t.strip()


def _render_rows(rows: List[Dict], limit: int = 8) -> str:
    """Human-readable fallback when the LLM summary is empty/unavailable.

    Never show a raw Python dict to a user. Renders the first `limit` rows as
    compact lines, dropping None-valued columns so the signal isn't buried.
    """
    if not rows:
        return "The query returned no results."
    lines = [f"Found {len(rows)} result{'s' if len(rows) != 1 else ''}."]
    for r in rows[:limit]:
        parts = [f"{k}: {v}" for k, v in r.items() if v is not None and v != ""]
        if parts:
            lines.append("• " + ", ".join(parts))
    if len(rows) > limit:
        lines.append(f"…and {len(rows) - limit} more.")
    return "\n".join(lines)


def _raise_if_not_json(resp, backend: str) -> None:
    """
    Turn a non-JSON LLM response into an actionable error instead of the cryptic
    "Expecting value: line 1 column 1 (char 0)" from resp.json().

    The DGX sits behind Cloudflare Access, which answers an UNauthenticated
    request with a 302 to an HTML login page — not JSON. With allow_redirects
    disabled that surfaces here as a 3xx or an HTML 200. Either way the token
    isn't authenticating: the app is not sending the CF-Access headers, or the
    Access app lacks a Service Auth policy covering this path/method.
    """
    ctype = resp.headers.get("content-type", "")
    if resp.is_redirect or (resp.status_code == 200 and "application/json" not in ctype):
        loc = resp.headers.get("location", "")
        hint = ""
        if "cloudflareaccess.com" in loc or "cf-access" in " ".join(resp.headers).lower():
            hint = (" — Cloudflare Access rejected the request. Check that the "
                    "service token is sent (CF-Access-Client-Id/Secret) and that "
                    "the Access app has a Service Auth policy for this path.")
        raise RuntimeError(
            f"{backend} backend returned non-JSON (HTTP {resp.status_code}, "
            f"content-type '{ctype or 'none'}'){hint}"
        )


# ── Capacity-forecast intent (answered by the projection engine, not SQL) ─────
#
# "How full will array X be by December?" / "when will the fleet run out of
# space?" are extrapolation questions. The LLM can write SQL to read history but
# can't reliably project it forward, so we detect the intent, resolve the array +
# target date deterministically, compute the forecast with capacity_projection,
# and let the LLM only NARRATE the exact numbers.

_FORECAST_KEYWORDS = (
    "forecast", "project", "projection", "predict", "how full will",
    "when will", "run out", "out of space", "fill up", "fills up", "will fill",
    "reach capacity", "days to full", "when full", "consume by", "consumed by",
    "use by", "grow to", "on track to", "at this rate", "extrapolate",
)

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _forecast_intent(question: str) -> bool:
    q = (question or "").lower()
    return any(k in q for k in _FORECAST_KEYWORDS)


def _parse_target_date(question: str, now: Optional[datetime] = None) -> Optional[str]:
    """Best-effort target-date extraction → 'YYYY-MM-DD', or None if none found."""
    q = (question or "").lower()
    now = now or datetime.now()

    # Explicit ISO date: 2026-12-01 or 2026-12-1
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", q)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # "in 6 months" / "6 months from now" / "in 90 days" / "in 2 years"
    m = re.search(r"(?:in\s+)?(\d+)\s*(day|week|month|year)s?(?:\s+from now)?", q)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        factor = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        return (now + timedelta(days=n * factor)).strftime("%Y-%m-%d")

    # "end of year" / "eoy" / "year end"
    if any(p in q for p in ("end of year", "eoy", "year end", "year-end")):
        return datetime(now.year, 12, 31).strftime("%Y-%m-%d")

    # "next year"
    if "next year" in q:
        return (now + timedelta(days=365)).strftime("%Y-%m-%d")

    # Month name, optionally with a year: "by december", "december 2026"
    for name, mo in _MONTHS.items():
        if re.search(rf"\b{name}\b", q):
            ym = re.search(rf"\b{name}\b\s+(20\d{{2}})", q)
            if ym:
                year = int(ym.group(1))
            else:
                # nearest future occurrence of that month, on the 1st
                year = now.year if mo >= now.month else now.year + 1
            return datetime(year, mo, 1).strftime("%Y-%m-%d")

    return None


def _known_array_names() -> List[str]:
    try:
        with get_db_cursor() as cursor:
            cursor.execute(f"SELECT array_name FROM {SCHEMA}.metrics_current WITH (NOLOCK)")
            return [r[0] for r in cursor.fetchall() if r and r[0]]
    except Exception:
        return []


def _resolve_forecast_array(question: str, names: List[str]) -> Optional[str]:
    """Longest known array_name that appears in the question (case-insensitive)."""
    q = (question or "").lower()
    hits = [n for n in names if len(n) >= 3 and n.lower() in q]
    return max(hits, key=len) if hits else None


def _forecast_sentence(fc: Dict[str, Any], target: Optional[str]) -> str:
    """Deterministic natural-language forecast — the narration fallback and the
    ground truth the LLM must not deviate from."""
    scope = fc.get("array_name") or "The fleet"
    cur = f"{fc['current_used_tb']:,} TB ({fc['current_pct']}% of {fc['usable_tb']:,} TB usable)"
    parts = []
    if target and fc.get("projected_used_tb") is not None:
        pct = fc["projected_pct"]
        chg = fc["projected_change_tb"]
        over = pct is not None and pct >= 100
        pct_txt = "over 100% (full before then)" if over else f"{pct}%"
        parts.append(
            f"{scope} is projected to reach {fc['projected_used_tb']:,} TB ({pct_txt}) by "
            f"{target} — a change of {'+' if chg >= 0 else ''}{chg:,} TB from today's {cur}."
        )
    else:
        parts.append(f"{scope} is currently at {cur}, {fc['trend']} at "
                     f"{fc['rate_tb_per_day']:+,} TB/day.")
    if fc.get("days_to_full") is not None:
        parts.append(f"At the current rate it fills around {fc['projected_full_date']} "
                     f"(~{fc['days_to_full']} days).")
    elif fc.get("trend") != "growing":
        parts.append("It is not currently trending toward full.")
    basis = f"Basis: {fc['window_days']}-day trend ({fc['data_points']} daily points)."
    caveat = f" {fc['caveat']}." if fc.get("caveat") else ""
    return " ".join(parts) + " " + basis + caveat


class ChatService:
    """Orchestrates the question → SQL → answer pipeline.

    The LLM backend is injectable so the same pipeline can be pointed at more
    than one model — used to benchmark the CPU-hosted qwen2.5:3b against the DGX
    Spark's qwen3:30b-a3b on identical questions. Defaults to the local backend,
    so existing callers (ChatService()) are unchanged.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        name: str = "local",
    ):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        # Extra headers per backend — the DGX sits behind Cloudflare Access and
        # needs a service token on EVERY request (an Access app with only
        # Service Auth policies issues no reusable cookie).
        self.headers = headers or {}
        self.name = name
        # qwen3 is a reasoning model. Observed live: its <think> chain eats the
        # num_predict budget and the SQL/answer never appears (empty responses),
        # and the Ollama `think:false` flag was NOT honored by this endpoint. The
        # reliable switch is qwen3's own /no_think token in the prompt. qwen2.5
        # (local) has no reasoning mode, so we only inject it for reasoning models.
        self.reasoning = "qwen3" in (self.model or "").lower()

    def _no_think(self, prompt: str) -> str:
        """Prepend qwen3's /no_think switch for reasoning models; no-op otherwise."""
        return f"/no_think\n{prompt}" if self.reasoning else prompt

    @classmethod
    def for_backend(cls, backend: str = "local") -> "ChatService":
        """Build a ChatService for a named backend: 'local' or 'dgx'."""
        if backend == "local":
            return cls(name="local")
        if backend == "dgx":
            if not settings.chat_dgx_base_url:
                raise ValueError(
                    "CHAT_DGX_BASE_URL is not set — the DGX backend is opt-in "
                    "because it sends data off-network."
                )
            headers = {}
            if settings.cf_access_client_id and settings.cf_access_client_secret:
                headers = {
                    "CF-Access-Client-Id": settings.cf_access_client_id,
                    "CF-Access-Client-Secret": settings.cf_access_client_secret,
                }
            return cls(
                base_url=settings.chat_dgx_base_url,
                model=settings.chat_dgx_model,
                headers=headers,
                name="dgx",
            )
        raise ValueError(f"Unknown chat backend '{backend}' (expected 'local' or 'dgx')")

    def ask(self, question: str, context: List[Dict] = None) -> Dict[str, Any]:
        """
        Process a user question end-to-end.

        Returns dict with: answer, sql, rows, duration_ms, model, error
        """
        start = time.time()
        result = {
            "answer": "",
            "sql": "",
            "rows": 0,
            "duration_ms": 0,
            "model": self.model,
            "error": None,
        }

        try:
            # Forecast questions are answered by the deterministic projection
            # engine, not SQL — the LLM only narrates the computed numbers.
            if _forecast_intent(question):
                fc_result = self._answer_forecast(question)
                fc_result["duration_ms"] = int((time.time() - start) * 1000)
                fc_result["model"] = self.model
                return fc_result

            # Step 1: Generate SQL from the question
            sql = self._generate_sql(question, context)
            if not sql:
                result["error"] = "LLM did not return a SQL query"
                result["answer"] = "I wasn't able to generate a query for that question. Could you rephrase it?"
                return result

            result["sql"] = sql

            # Step 2: Validate SQL safety
            is_safe, reason = validate_sql(sql)
            if not is_safe:
                logger.warning(f"SQL rejected: {reason} — SQL: {sql}")
                result["error"] = f"Query rejected: {reason}"
                result["answer"] = "I generated a query but it was rejected by the safety filter. Please try a different question."
                return result

            # Step 3: Add safety limits and execute (with retry on SQL error)
            safe_sql = add_safety_limits(sql)
            rows = None
            sql_error = None

            try:
                rows = self._execute_sql(safe_sql)
            except Exception as sql_err:
                sql_error = str(sql_err)
                logger.warning(f"SQL execution failed, attempting self-correction: {sql_error}")

            # Step 3b: If SQL failed, ask LLM to fix it
            if sql_error and rows is None:
                fixed_sql = self._fix_sql(question, safe_sql, sql_error)
                if fixed_sql:
                    is_safe2, _ = validate_sql(fixed_sql)
                    if is_safe2:
                        safe_sql = add_safety_limits(fixed_sql)
                        result["sql"] = safe_sql
                        try:
                            rows = self._execute_sql(safe_sql)
                        except Exception as e2:
                            logger.error(f"Retry also failed: {e2}")
                            result["error"] = f"SQL error (even after retry): {e2}"
                            result["answer"] = "I had trouble querying the database. The column or table name may not match. Please try rephrasing your question."
                            result["duration_ms"] = int((time.time() - start) * 1000)
                            return result
                else:
                    result["error"] = sql_error
                    result["answer"] = "I had trouble querying the database. Please try rephrasing your question."
                    result["duration_ms"] = int((time.time() - start) * 1000)
                    return result

            if rows is None:
                rows = []

            result["rows"] = len(rows)

            # Step 4: Format results into natural language
            answer = self._format_answer(question, safe_sql, rows)
            result["answer"] = answer

        except Exception as e:
            logger.error(f"Chat error: {e}")
            result["error"] = str(e)
            result["answer"] = f"Sorry, I encountered an error: {e}"

        result["duration_ms"] = int((time.time() - start) * 1000)
        return result

    def _answer_forecast(self, question: str) -> Dict[str, Any]:
        """Resolve array + target date, compute the projection, narrate it."""
        result = {"answer": "", "sql": "", "rows": 0, "duration_ms": 0,
                  "model": self.model, "error": None}
        array = _resolve_forecast_array(question, _known_array_names())
        target = _parse_target_date(question)
        fc = forecast_capacity(array, target)
        scope = array or "fleet"
        result["sql"] = f"-- capacity forecast · scope={scope} · target={target or 'when-full'}"
        result["rows"] = fc.get("data_points", 0)

        if fc.get("error"):
            result["answer"] = fc["error"]
            return result

        deterministic = _forecast_sentence(fc, target)
        # Let the LLM phrase it, but ONLY from the computed facts (it cannot invent
        # numbers). Fall back to the deterministic sentence if narration fails.
        result["answer"] = self._narrate_forecast(question, fc, deterministic)
        return result

    def _narrate_forecast(self, question: str, fc: Dict[str, Any], fallback: str) -> str:
        # Rephrase the deterministic summary conversationally — do NOT hand the LLM
        # raw field names (an earlier version passed a JSON dict and the model
        # parroted "window_days-day"). Numbers/dates must survive verbatim.
        prompt = (
            "Rephrase the following storage-capacity summary as a direct, natural "
            "2-3 sentence answer to the user's question. Keep every number, "
            "percentage, and date EXACTLY as written — do not recompute or round. "
            "Do not add facts that aren't in the summary.\n\n"
            f"Question: {question}\nSummary: {fallback}\n\nAnswer:"
        )
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": self._no_think(prompt),
                      "stream": False, "options": {"temperature": 0.2, "num_predict": 512}},
                timeout=180, headers=self.headers, allow_redirects=False,
            )
            _raise_if_not_json(resp, self.name)
            resp.raise_for_status()
            answer = _strip_think(resp.json().get("response", "")).strip()
            if answer:
                return answer
        except Exception as e:
            logger.warning(f"Forecast narration failed, using deterministic text: {e}")
        return fallback

    def _generate_sql(self, question: str, context: List[Dict] = None) -> str:
        """Send question to Ollama, get SQL back."""
        # Only send the CMS schema when the question needs it — see
        # build_system_prompt(). Storage questions keep the ~7.2k prompt; CMS
        # questions get ~14.6k, which on the CPU 3b is the difference between a
        # usable answer and 78.7s for this call alone.
        messages = [{"role": "system", "content": self._no_think(build_system_prompt(question))}]

        # Add conversation context if provided (for multi-turn)
        if context:
            for turn in context[-4:]:  # Keep last 4 turns max
                messages.append({"role": "user", "content": turn.get("question", "")})
                if turn.get("sql"):
                    messages.append({"role": "assistant", "content": turn["sql"]})

        messages.append({"role": "user", "content": question})

        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    # Generous headroom: even with /no_think a reasoning model may
                    # emit a short think block, and truncating it produced EMPTY
                    # SQL responses live ("did not return a SQL query").
                    "num_predict": 1024,
                },
            },
            timeout=180,
            headers=self.headers,        # CF-Access token for the DGX backend
            allow_redirects=False,       # a CF 302 is an auth failure, not JSON
        )
        _raise_if_not_json(resp, self.name)
        resp.raise_for_status()
        data = resp.json()

        raw = data.get("message", {}).get("content", "")
        logger.info(f"LLM raw response: {raw[:200]}")

        return extract_sql_from_response(raw)

    def _fix_sql(self, question: str, failed_sql: str, error: str) -> Optional[str]:
        """Ask the LLM to fix a SQL query that failed execution."""
        fix_prompt = (
            f"The following SQL query failed with an error.\n\n"
            f"Original question: {question}\n"
            f"Failed SQL: {failed_sql}\n"
            f"Error: {error}\n\n"
            f"Please fix the SQL query. Remember:\n"
            f"- Only use columns that exist in the schema provided earlier\n"
            f"- The {SCHEMA}.metrics_current table has: capacity_total, capacity_used, capacity_used_pct (NOT 'used' or 'total')\n"
            f"- The {SCHEMA}.volumes_cache table uses 'size' and 'used' (NOT capacity_total/capacity_used)\n"
            f"- Return ONLY the corrected SQL query, nothing else.\n"
        )
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": self._no_think(build_system_prompt(question) + "\n\n" + fix_prompt),
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 1024},
                },
                timeout=180,
                headers=self.headers,
                allow_redirects=False,
            )
            _raise_if_not_json(resp, self.name)
            resp.raise_for_status()
            fixed = extract_sql_from_response(resp.json().get("response", ""))
            logger.info(f"LLM self-corrected SQL: {fixed[:200]}")
            return fixed
        except Exception as e:
            logger.error(f"SQL fix attempt failed: {e}")
            return None

    def _execute_sql(self, sql: str) -> List[Dict]:
        """Execute validated SQL and return results."""
        with get_db_cursor() as cursor:
            cursor.execute(sql)
            return rows_to_dicts(cursor, cursor.fetchall())

    def _format_answer(self, question: str, sql: str, rows: List[Dict]) -> str:
        """
        Use a short LLM call to format results into natural language.
        With KEEP_ALIVE=24h the model stays warm, so this adds only ~3-5s.
        Falls back to simple formatting if LLM fails.
        """
        if not rows:
            return "The query returned no results."

        # Build a compact data representation
        results_str = json.dumps(rows[:15], default=str, indent=2)
        if len(rows) > 15:
            results_str += f"\n... ({len(rows) - 15} more rows)"

        prompt = (
            f"Summarize these SQL query results in 2-3 clear sentences for a "
            f"storage administrator. Use exact numbers. Format bytes as TB/GB. "
            f"Format large numbers with commas. Convert microseconds to ms if "
            f">1000. Answer directly — do not restate the question.\n\n"
            f"Question: {question}\n"
            f"Data ({len(rows)} rows):\n{results_str}\n\n"
            f"Answer:"
        )

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    # /no_think (via _no_think) is the reliable qwen3 switch — the
                    # Ollama "think": false flag was NOT honored by this endpoint
                    # (reasoning still leaked verbatim into answers). num_predict is
                    # generous so a stray think block can't truncate the answer.
                    "prompt": self._no_think(prompt),
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1024},
                },
                timeout=180,
                headers=self.headers,
                allow_redirects=False,
            )
            _raise_if_not_json(resp, self.name)
            resp.raise_for_status()
            answer = _strip_think(resp.json().get("response", ""))
            if answer:
                return answer
            logger.warning("LLM format returned empty after strip; using fallback")
        except Exception as e:
            logger.warning(f"LLM format failed, using fallback: {e}")

        # Fallback: readable rows, never a raw dict.
        return _render_rows(rows)

    def check_ollama(self) -> Dict[str, Any]:
        """Health check — is Ollama running and model loaded?"""
        try:
            resp = requests.get(
                f"{self.base_url}/api/tags", timeout=5,
                headers=self.headers, allow_redirects=False,
            )
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                has_model = any(self.model in m for m in models)
                return {
                    "status": "ready" if has_model else "model_missing",
                    "models": models,
                    "required_model": self.model,
                }
        except Exception as e:
            return {"status": "offline", "error": str(e)}
        return {"status": "error"}
