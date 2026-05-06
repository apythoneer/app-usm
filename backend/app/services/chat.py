"""
Chat Service — orchestrates natural language → SQL → answer pipeline.
Uses a local Ollama LLM (no data leaves the host).
"""

import json
import logging
import time
import requests
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.db.session import get_db_cursor, rows_to_dicts
from app.services.sql_safety import validate_sql, extract_sql_from_response, add_safety_limits

logger = logging.getLogger("usm.chat")
settings = get_settings()
SCHEMA = settings.db_schema

# ── Schema context for the LLM ─────────────────────────────────────────────

_SCHEMA_CONTEXT = f"""
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

TABLE {SCHEMA}.managed_arrays — Array inventory managed via UI
  Columns: array_name NVARCHAR(255) [unique], vendor, group_label NVARCHAR(100),
  enabled BIT, cred_key NVARCHAR(255)

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
""".strip()

_SYSTEM_PROMPT = f"""You are a storage infrastructure analyst assistant for the Unified Storage Monitoring (USM) platform.
You help answer questions about storage arrays, volumes, hosts, alerts, and capacity by writing SQL Server (T-SQL) queries.

{_SCHEMA_CONTEXT}

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


class ChatService:
    """Orchestrates the question → SQL → answer pipeline."""

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model

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

    def _generate_sql(self, question: str, context: List[Dict] = None) -> str:
        """Send question to Ollama, get SQL back."""
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

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
                    "num_predict": 500,
                },
            },
            timeout=180,
        )
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
                    "prompt": _SYSTEM_PROMPT + "\n\n" + fix_prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 500},
                },
                timeout=180,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            fixed = extract_sql_from_response(raw)
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
            f"Summarize these SQL query results in 2-3 clear sentences. "
            f"Use exact numbers. Format bytes as TB/GB. Format large numbers with commas. "
            f"Convert microseconds to ms if >1000.\n\n"
            f"Question: {question}\n"
            f"Data ({len(rows)} rows):\n{results_str}\n\n"
            f"Answer:"
        )

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 200},
                },
                timeout=180,
            )
            resp.raise_for_status()
            answer = resp.json().get("response", "").strip()
            if answer:
                return answer
        except Exception as e:
            logger.warning(f"LLM format failed, using fallback: {e}")

        # Fallback: simple key-value format
        if len(rows) == 1:
            return ", ".join(f"{k}: {v}" for k, v in rows[0].items())
        return f"Found {len(rows)} results. First: {rows[0]}"

    def check_ollama(self) -> Dict[str, Any]:
        """Health check — is Ollama running and model loaded?"""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
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
