"""
Similar-question cache for the Text-to-SQL path.

The slow part of a Text-to-SQL answer is the model writing the SQL (~10-80 s on
the sandbox CPU). Two cheap reuses, both backed by USM.chat_log, so every
backend process shares them with no extra store:

    fresh answer   a near-identical question answered (no error, rows > 0, not 👎)
                   within CHAT_ANSWER_CACHE_TTL_S -> return that answer as-is
    👍 SQL          a near-identical question whose answer got 👍, any age ->
                   re-run its SQL for fresh data, skipping SQL generation

"Near-identical" = cosine >= CHAT_ANSWER_CACHE_SIM on nomic-embed-text vectors
AND an identical signature: the same array, vendor, period, top-N, %, every
number in the question, and the same contrast words (read/write,
highest/lowest, ...). Embeddings alone can't tell "over 80%" from "over 90%".
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.core.config import get_settings

logger = logging.getLogger("usm.chat.cache")
settings = get_settings()
SCHEMA = settings.db_schema

_CONTRAST = frozenset({
    "read", "write", "min", "max", "minimum", "maximum", "lowest", "highest", "least", "most",
    "top", "bottom", "not", "no", "without", "never", "increase", "decrease", "grow", "grew",
    "growing", "shrink", "shrinking", "critical", "warning", "info", "open", "closed", "resolved",
    "average", "avg", "peak", "total", "free", "used", "before", "after", "above", "below",
    "over", "under", "more", "less", "fewer", "first", "last",
})


def signature(question: str) -> tuple:
    from app.services.chat_intents import extract_params
    q = " ".join((question or "").lower().split())
    p = extract_params(q)
    nums = tuple(sorted(set(re.findall(r"\d+(?:\.\d+)?", q))))
    words = tuple(sorted(set(re.findall(r"[a-z]+", q)) & _CONTRAST))
    return (p.array, p.vendor, p.days, p.n, p.pct, nums, words)


def _candidates() -> List[Dict[str, Any]]:
    from app.db.session import get_db_cursor
    with get_db_cursor() as cur:
        cur.execute(
            f"""SELECT TOP 300 id, question, sql_text, answer, row_count, rating,
                       DATEDIFF(SECOND, asked_at, SYSUTCDATETIME()) AS age_s
                FROM {SCHEMA}.chat_log WITH (NOLOCK)
                WHERE route IN ('sql', 'sql-golden') AND error IS NULL AND row_count > 0
                  AND sql_text IS NOT NULL AND ISNULL(rating, 0) >= 0
                  AND (rating = 1 OR asked_at >= DATEADD(SECOND, -?, SYSUTCDATETIME()))
                ORDER BY id DESC""",
            int(settings.chat_answer_cache_ttl_s),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def lookup(question: str) -> Optional[Dict[str, Any]]:
    """Best reusable earlier answer, or None. Never raises."""
    try:
        from app.services.chat_intents import _vectors
        from app.services.embeddings import cosine, query_prefix
        cands = _candidates()
        if not cands:
            return None
        sig = signature(question)
        same = [c for c in cands if signature(c["question"]) == sig]
        if not same:
            return None
        norm = lambda t: query_prefix() + " ".join(t.lower().split())  # noqa: E731
        vecs = _vectors([norm(question)] + [norm(c["question"]) for c in same])
        qv, best, best_s = vecs[0], None, 0.0
        for c, v in zip(same, vecs[1:]):
            s = cosine(qv, v)
            # prefer a 👍 source on ties; otherwise the most similar
            if s > best_s + 1e-9 or (abs(s - best_s) < 1e-9 and c.get("rating") == 1):
                best, best_s = c, s
        if best is None or best_s < settings.chat_answer_cache_sim:
            return None
        fresh = (best["age_s"] or 0) <= settings.chat_answer_cache_ttl_s
        kind = "answer" if fresh else "sql"   # only 👍 rows can be older than the TTL
        logger.info("answer cache hit (%s) from #%s, similarity %.3f", kind, best["id"], best_s)
        return {"kind": kind, "from_id": best["id"], "similarity": round(best_s, 3),
                "age_min": round((best["age_s"] or 0) / 60, 1), "sql": best["sql_text"],
                "answer": best["answer"], "rows": best["row_count"], "rated": best.get("rating") == 1}
    except Exception as e:
        logger.warning("answer cache lookup skipped: %s", type(e).__name__)
        return None
