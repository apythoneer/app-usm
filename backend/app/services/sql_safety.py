"""
SQL Safety Validator — ensures LLM-generated SQL is read-only and scoped to USM schema.
Defense-in-depth: regex blocklist + structural validation.
"""

import re
import logging
from typing import Tuple

logger = logging.getLogger("usm.chat.safety")

# Patterns that MUST NOT appear in generated SQL
_BLOCKED_PATTERNS = [
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|MERGE)\b',
    r'\b(EXEC|EXECUTE|CALL)\b',
    r'\b(xp_|sp_)\w+',
    r'\b(OPENROWSET|OPENDATASOURCE|OPENQUERY|BULK)\b',
    r'\b(BACKUP|RESTORE|SHUTDOWN|RECONFIGURE)\b',
    r'\b(GRANT|REVOKE|DENY)\b',
    r'\b(INTO\s+OUTFILE|LOAD_FILE|LOAD\s+DATA)\b',
    r'\bWAITFOR\b',
    r';\s*\S',           # Multiple statements (after semicolon)
]

# Only these table prefixes are allowed
_ALLOWED_SCHEMA = "USM"

_ALLOWED_TABLES = {
    # USM core
    "metrics_current", "metrics_history", "messages",
    "volumes_cache", "hosts_cache", "host_groups_cache",
    "protection_groups_cache", "managed_arrays",
    "daily_stats", "app_settings",
    # Per-volume time-series (described in the chat schema; without this entry
    # every volume-growth question is rejected by validate_sql).
    "volumes_history",
    # CMS/CMDB bridge views (USM.vw_cms_*). Read-only views over dbo.CMS*, with
    # the ASSIGNMENT filters and joins baked in — see backend/sql/cms_views.sql.
    # They expose no credential columns.
    "vw_cms_array_to_app_db",
    "vw_cms_app_to_server",
    "vw_cms_app_to_database",
    "vw_cms_database_to_server",
    "vw_cms_app_to_database_to_server",
    "vw_cms_server_to_cluster",
    "vw_cms_vm_to_host",
    "vw_cms_server_to_backups",
    "vw_cms_switch_to_host_app_db",
}


def validate_sql(sql: str) -> Tuple[bool, str]:
    """
    Validate that LLM-generated SQL is safe to execute.

    Returns:
        (is_safe, reason) — True if safe, False with explanation if rejected.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL"

    cleaned = sql.strip().rstrip(";").strip()

    # Must start with SELECT (or WITH for CTEs)
    upper = cleaned.upper().lstrip()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False, f"Only SELECT queries allowed. Got: {upper[:20]}..."

    # Check blocked patterns
    for pattern in _BLOCKED_PATTERNS:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return False, f"Blocked pattern detected: {match.group()}"

    # Verify only USM schema tables are referenced
    # Look for table references: FROM/JOIN followed by schema.table or just table
    table_refs = re.findall(
        r'\b(?:FROM|JOIN)\s+(?:(\w+)\.)?(\w+)\b',
        cleaned, re.IGNORECASE
    )
    for schema, table in table_refs:
        if schema and schema.upper() != _ALLOWED_SCHEMA:
            return False, f"Access to schema '{schema}' is not allowed. Only {_ALLOWED_SCHEMA}.* tables."
        if table.lower() in ("sysobjects", "syscolumns", "sys", "information_schema",
                              "syslogins", "sysprocesses", "sysservers"):
            return False, f"Access to system table '{table}' is blocked."
        # If schema is explicitly specified, validate table name
        if schema and table.lower() not in _ALLOWED_TABLES:
            return False, f"Table '{schema}.{table}' is not in the allowed list."

    logger.debug(f"SQL validated OK: {cleaned[:80]}...")
    return True, "OK"


def extract_sql_from_response(text: str) -> str:
    """
    Extract SQL query from LLM response text.
    Handles markdown code blocks, plain SQL, and mixed text.
    """
    # Strip reasoning-model <think>...</think> first, so we don't extract a
    # SELECT the model was merely reasoning about rather than its final query.
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)

    # Try ```sql ... ``` blocks first
    sql_blocks = re.findall(r'```(?:sql)?\s*\n?(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        return sql_blocks[0].strip()

    # Try to find SELECT statement in the text
    match = re.search(
        r'((?:WITH|SELECT)\b.*?)(?:\n\n|\Z)',
        text, re.DOTALL | re.IGNORECASE
    )
    if match:
        return match.group(1).strip().rstrip(";")

    # Return the whole thing stripped
    return text.strip().rstrip(";")


def add_safety_limits(sql: str, max_rows: int = 100) -> str:
    """Add TOP N if not already present to prevent unbounded result sets."""
    upper = sql.upper().strip()
    if "TOP " not in upper[:50]:
        # Insert TOP after SELECT
        sql = re.sub(
            r'\bSELECT\b',
            f'SELECT TOP {max_rows}',
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
    return sql
