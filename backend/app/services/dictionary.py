"""
Data dictionary for the Storage-AI Text-to-SQL chatbot (SCPSB-946 / chatbot
performance plan, item 1: "tag the data").

WHAT THIS IS
    A git-versioned, structured semantic layer over the USM schema: for the
    tables/columns the model is asked about, a plain-English description, unit,
    and the *synonyms* real users type ("usable", "full", "how big"). It is
    materialised into an additive `USM.usm_dictionary` table (so it is queryable
    and versioned with the schema) and retrieved into the chat prompt as
    schema-linking context.

WHY (small-model Text-to-SQL)
    The #1 reason a 3B CPU model writes wrong SQL is it doesn't know what opaque
    columns MEAN (`capacity_used_pct`, `data_reduction`, vendor codes). Feeding
    the *relevant* dictionary entries into the prompt is the single biggest
    accuracy lever — see docs/chatbot-performance.md §1.

GUARDRAILS (docs/chatbot-performance.md §0 — do NOT degrade other consumers)
    - Additive only: this is a NEW table; nothing existing is altered/dropped.
    - Read-only w.r.t. the schema it describes.
    - OFF by default: retrieval is injected into the prompt ONLY when
      settings.chat_use_dictionary is true, so chat behaviour is unchanged until
      we have an eval set and have measured that forecasting/analytics are
      unaffected.

PGVECTOR SEAM (post SCPSB-1022 Postgres cutover)
    `retrieve_dictionary_context()` is the swap point. Today it scores entries by
    keyword/synonym overlap (no embeddings, works on the current SQL Server).
    After the Postgres cutover, replace ONLY the scoring in `_score_entry()` /
    `retrieve_dictionary_context()` with pgvector cosine similarity over
    precomputed entry embeddings — the return shape (a prompt text block) stays
    identical, so chat.py needs no change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.core.config import get_settings

logger = logging.getLogger("usm.dictionary")
settings = get_settings()
SCHEMA = settings.db_schema

# Bump when the seed below changes so seed_dictionary() re-materialises the table.
DICTIONARY_VERSION = "2026-10-20.1"


@dataclass(frozen=True)
class Entry:
    """One dictionary entry: a table, or a column within a table."""
    table: str
    column: Optional[str]            # None => table-level entry
    description: str
    unit: str = ""                   # e.g. "bytes", "TB", "%", "microseconds"
    synonyms: tuple[str, ...] = ()    # words/phrases a user might type
    examples: str = ""               # sample values, free text

    @property
    def entry_type(self) -> str:
        return "column" if self.column else "table"


# ── The dictionary (curated; grounded in chat.py _SCHEMA_CORE columns) ─────────
# Start scope: the core storage tables. Extend table-by-table; keep entries
# grounded in REAL columns (never invent a column the schema doesn't have).
_DICTIONARY: List[Entry] = [
    # metrics_current ----------------------------------------------------------
    Entry("metrics_current", None,
          "Latest metrics snapshot per storage array (one row per array, refreshed ~60s). "
          "Use for current/now questions: utilisation, live IOPS/latency, status.",
          synonyms=("current", "now", "latest", "live", "snapshot", "right now")),
    Entry("metrics_current", "capacity_used_pct",
          "How full an array is, as a percent of total capacity.",
          unit="%", synonyms=("utilization", "utilisation", "utilized", "utilised", "util",
                              "how full", "percent full", "used percent", "percent used",
                              "fullness", "usage", "over capacity"), examples="73.4, 91.0"),
    Entry("metrics_current", "capacity_total",
          "Total usable capacity of the array.",
          unit="bytes", synonyms=("total capacity", "size", "usable", "how big",
                                  "total space"), examples="100000000000000"),
    Entry("metrics_current", "capacity_used",
          "Capacity currently consumed on the array.",
          unit="bytes", synonyms=("used", "consumed", "used space", "space used")),
    Entry("metrics_current", "data_reduction",
          "Data-reduction ratio from dedup+compression (data only, excludes snapshots/thin).",
          unit="ratio (x)", synonyms=("dedup", "compression", "reduction", "efficiency",
                                      "data reduction ratio"), examples="3.2, 5.1"),
    Entry("metrics_current", "total_reduction",
          "Total reduction ratio including thin provisioning and snapshots.",
          unit="ratio (x)", synonyms=("total reduction", "overall reduction")),
    Entry("metrics_current", "read_iops",
          "Current read operations per second.",
          unit="IOPS", synonyms=("read iops", "reads", "read ops")),
    Entry("metrics_current", "write_iops",
          "Current write operations per second.",
          unit="IOPS", synonyms=("write iops", "writes", "write ops")),
    Entry("metrics_current", "read_latency_us",
          "Current average read latency.",
          unit="microseconds", synonyms=("read latency", "latency", "response time")),
    Entry("metrics_current", "write_latency_us",
          "Current average write latency.",
          unit="microseconds", synonyms=("write latency", "latency")),
    Entry("metrics_current", "vendor",
          "Storage vendor / platform code for the array.",
          synonyms=("vendor", "platform", "make", "type"),
          examples="pure, netapp, hitachi, hpe, dell, oracle"),
    Entry("metrics_current", "array_status",
          "Overall health/status of the array.",
          synonyms=("status", "health", "state")),

    # volumes_cache ------------------------------------------------------------
    Entry("volumes_cache", None,
          "Volume inventory per array (refreshed ~15min). Use for 'what volumes on array X', "
          "volume sizes, per-volume reduction.",
          synonyms=("volume", "volumes", "lun", "luns", "vols")),
    Entry("volumes_cache", "size",
          "Provisioned size of the volume.",
          unit="bytes", synonyms=("volume size", "provisioned", "capacity")),
    Entry("volumes_cache", "used",
          "Space actually consumed by the volume.",
          unit="bytes", synonyms=("volume used", "consumed")),
    Entry("volumes_cache", "snapshots",
          "Number of snapshots on the volume.",
          unit="count", synonyms=("snapshot count", "snaps", "number of snapshots")),

    # hosts_cache --------------------------------------------------------------
    Entry("hosts_cache", None,
          "Host / initiator inventory per array. Use for 'what hosts are on array X', "
          "WWN/IQN/NQN lookups. Prefer this over the volumes_cache.hosts JSON.",
          synonyms=("host", "hosts", "initiator", "server", "connected hosts")),
    Entry("hosts_cache", "wwn",
          "Fibre-Channel World Wide Name(s) of the host.",
          synonyms=("wwn", "world wide name", "fc", "fibre channel")),
    Entry("hosts_cache", "iqn",
          "iSCSI Qualified Name of the host.",
          synonyms=("iqn", "iscsi")),
    Entry("hosts_cache", "nqn",
          "NVMe Qualified Name of the host.",
          synonyms=("nqn", "nvme")),

    # messages -----------------------------------------------------------------
    Entry("messages", None,
          "Alert / event history per array. Use for alerts, events, tickets. "
          "Open alerts = resolved=0 AND suppressed=0.",
          synonyms=("alert", "alerts", "event", "events", "incident", "ticket", "message")),
    Entry("messages", "severity",
          "Alert severity level.",
          synonyms=("severity", "critical", "warning", "info", "priority")),
    Entry("messages", "resolved",
          "Whether the alert is resolved (1) or still open (0).",
          synonyms=("resolved", "open", "closed", "active", "cleared")),
    Entry("messages", "snow_ticket",
          "Associated ServiceNow ticket number, if one was opened.",
          synonyms=("servicenow", "snow", "ticket", "incident number")),

    # managed_arrays -----------------------------------------------------------
    Entry("managed_arrays", None,
          "Array inventory + lifecycle (synced nightly from the CMS source of record). "
          "USE THIS for model/site/support/EOSL questions.",
          synonyms=("inventory", "lifecycle", "asset", "model", "site", "support", "fleet")),
    Entry("managed_arrays", "eosl_date",
          "End-of-service-life date for the array. Use for 'past EOSL', 'out of support'.",
          unit="date", synonyms=("eosl", "end of service life", "out of support",
                                 "end of life", "eol", "support end")),
    Entry("managed_arrays", "site",
          "Datacenter / site where the array lives.",
          synonyms=("site", "datacenter", "data center", "location", "dc")),
    Entry("managed_arrays", "model",
          "Hardware model of the array.",
          synonyms=("model", "hardware", "box type")),
    Entry("managed_arrays", "group_label",
          "Cloud/site grouping tag (CSP or on-prem site).",
          synonyms=("csp", "cloud", "aws", "azure", "gcp", "on-prem", "group")),
    Entry("managed_arrays", "disposition",
          "Asset disposition; 'Current' = active asset.",
          synonyms=("disposition", "active", "current", "decommissioned", "retired")),

    # daily_stats --------------------------------------------------------------
    Entry("daily_stats", None,
          "Aggregated daily fleet-wide summary (one row per day). Use for fleet totals/trends by day.",
          synonyms=("fleet", "daily", "summary", "totals", "aggregate", "overall")),

    # metrics_history ----------------------------------------------------------
    Entry("metrics_history", None,
          "Time-series performance/capacity per array. USE THIS for any historical/time-based "
          "question (last 24h, last 7 days, trends, peak, highest ever). collected_at is DATETIME2 "
          "(safe for DATEADD/DATEDIFF) — unlike metrics_current.collected_at (NVARCHAR).",
          synonyms=("history", "historical", "trend", "trends", "over time", "last 24 hours",
                    "last 7 days", "peak", "highest ever", "time series", "growth")),

    # volumes_history ----------------------------------------------------------
    Entry("volumes_history", None,
          "Per-volume time-series (one row per volume per ~30min). USE THIS for volume growth/shrink "
          "over time. VERY large (40M+ rows) — ALWAYS filter by collected_at and/or array/volume. "
          "Note: its 'snapshots' column is snapshot SPACE in bytes, NOT a count.",
          synonyms=("volume growth", "volume trend", "which volumes grew", "per-volume history")),
]


def _entry_text(e: Entry) -> str:
    """Render one entry as a compact prompt line."""
    name = f"{SCHEMA}.{e.table}.{e.column}" if e.column else f"{SCHEMA}.{e.table}"
    bits = [f"- {name}: {e.description}"]
    if e.unit:
        bits.append(f"[unit: {e.unit}]")
    if e.synonyms:
        bits.append(f"[aka: {', '.join(e.synonyms)}]")
    if e.examples:
        bits.append(f"[e.g. {e.examples}]")
    return " ".join(bits)


_WORD_RE = re.compile(r"[a-z0-9]+")

# Low-signal words to ignore when scoring token overlap: English stopwords plus
# domain words that appear in almost every entry ("array", "storage", ...), which
# would otherwise match everything and drown the real signal.
_STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "to", "for", "by", "is", "are", "was",
    "be", "with", "and", "or", "me", "my", "we", "our", "all", "any", "show",
    "list", "get", "give", "tell", "what", "which", "who", "how", "many", "much",
    "do", "does", "did", "has", "have", "that", "this", "it", "you", "i",
    "over", "under", "per", "each", "from", "at", "as",
    "array", "arrays", "storage", "data",
}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS}


def _score_entry(q_tokens: set[str], q_text: str, e: Entry) -> int:
    """Keyword/synonym overlap score for one entry.

    PGVECTOR SEAM: after the Postgres cutover this is replaced by cosine
    similarity between the question embedding and the entry embedding. The caller
    (`retrieve_dictionary_context`) is otherwise unchanged.
    """
    score = 0
    # multi-word synonyms matched as substrings (weighted highest)
    for syn in e.synonyms:
        if " " in syn and syn in q_text:
            score += 5
    syn_tokens = set()
    for syn in e.synonyms:
        syn_tokens |= _tokens(syn)
    score += 3 * len(q_tokens & syn_tokens)
    # table / column name hits
    score += 2 * len(q_tokens & _tokens(e.table))
    if e.column:
        score += 2 * len(q_tokens & _tokens(e.column))
    # description word overlap (weakest signal)
    score += len(q_tokens & _tokens(e.description))
    return score


def retrieve_dictionary_context(question: str, k: int = 8) -> str:
    """Return the top-k most relevant dictionary entries as a prompt text block.

    Static keyword/synonym retrieval today (works on SQL Server, no embeddings).
    Returns "" when nothing is relevant, so the caller can append unconditionally.
    """
    q_text = question.lower()
    q_tokens = _tokens(question)
    scored = [(e, _score_entry(q_tokens, q_text, e)) for e in _DICTIONARY]
    hits = sorted((es for es in scored if es[1] > 0), key=lambda es: es[1], reverse=True)[:k]
    if not hits:
        return ""
    lines = [_entry_text(e) for e, _ in hits]
    return "DATA DICTIONARY (relevant tables/columns for this question):\n" + "\n".join(lines)


# ── Materialisation into USM.usm_dictionary (additive, idempotent) ────────────

def seed_dictionary() -> None:
    """Materialise the dictionary into USM.usm_dictionary.

    Idempotent: if the table already holds the current DICTIONARY_VERSION, does
    nothing; otherwise wipes and re-inserts (the module is the source of truth).
    Called from the collector-role startup (RUN_SCHEDULER=true) after
    init_database(). Non-fatal by the caller's contract.
    """
    # Imported lazily so importing this module (e.g. for retrieval or tests) never
    # requires a DB connection.
    from app.db.session import get_db_cursor

    with get_db_cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {SCHEMA}.usm_dictionary WHERE source_version = ?",
            DICTIONARY_VERSION,
        )
        if cur.fetchone()[0] > 0:
            logger.info("usm_dictionary already at %s — skipping seed", DICTIONARY_VERSION)
            return

        cur.execute(f"DELETE FROM {SCHEMA}.usm_dictionary")
        for e in _DICTIONARY:
            cur.execute(
                f"""INSERT INTO {SCHEMA}.usm_dictionary
                    (entry_type, table_name, column_name, description, unit,
                     synonyms, example_values, source_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                e.entry_type, e.table, e.column, e.description, e.unit,
                ", ".join(e.synonyms), e.examples, DICTIONARY_VERSION,
            )
    logger.info("usm_dictionary seeded: %d entries @ %s", len(_DICTIONARY), DICTIONARY_VERSION)
