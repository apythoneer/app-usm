"""
Storage AI feedback loop: log every answer, take 👍/👎, and teach the intent
router new phrasings so it improves without a model change.

    chat_log              one row per answered question: route, timing, rating
    chat_intent_examples  learned phrasings, merged into chat_intents' examples

What a rating does (record_feedback):
    👍 on a vetted answer that needed the LLM check (borderline match)
        -> positive example, so the same phrasing routes instantly next time
    👎 alone               -> recorded only (may mean "data looks off", not "wrong type")
    👎 + "I wanted <intent>" -> positive example for <intent>, and a negative for
                              the intent that answered (if different)
    👎 + "none of these"   -> negative for the intent that answered (falls to SQL)
    👍 on a Text-to-SQL answer is only recorded — the future golden-query /
    semantic-cache source.

Everything here is assistive: failures are logged, never raised into chat.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.core.config import get_settings

logger = logging.getLogger("usm.chat.feedback")
settings = get_settings()
SCHEMA = settings.db_schema

_LEARNED_TTL = 120  # seconds between DB re-reads of learned examples (per process)
_learned: Dict[str, Any] = {"rows": [], "expires": 0.0}


def _cursor():
    from app.db.session import get_db_cursor
    return get_db_cursor()


def log_answer(question: str, result: Dict[str, Any]) -> Optional[int]:
    """Insert a chat_log row; returns its id (None if logging failed)."""
    try:
        with _cursor() as cur:
            cur.execute(
                f"""INSERT INTO {SCHEMA}.chat_log
                    (question, route, route_how, route_score, sql_text, answer,
                     row_count, duration_ms, error)
                    OUTPUT INSERTED.id
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                question[:1000], result.get("route"), result.get("route_how"),
                result.get("route_score"), result.get("sql") or None,
                (result.get("answer") or "")[:4000], int(result.get("rows") or 0),
                int(result.get("duration_ms") or 0), (result.get("error") or None) and str(result["error"])[:1000],
            )
            return int(cur.fetchone()[0])
    except Exception as e:
        logger.warning("chat_log insert failed: %s", type(e).__name__)
        return None


def _add_example(cur, question: str, intent: str, polarity: int, source: str, log_id: int) -> None:
    q = " ".join(question.lower().split())
    cur.execute(
        f"""IF NOT EXISTS (SELECT 1 FROM {SCHEMA}.chat_intent_examples
                           WHERE question = ? AND intent = ? AND polarity = ? AND active = 1)
            INSERT INTO {SCHEMA}.chat_intent_examples (question, intent, polarity, source, chat_log_id)
            VALUES (?, ?, ?, ?, ?)""",
        q, intent, polarity, q, intent, polarity, source, log_id,
    )


def record_feedback(log_id: int, rating: int, wanted_intent: Optional[str] = None,
                    comment: Optional[str] = None) -> Dict[str, Any]:
    """Store a rating and derive learned examples. Returns what was learned."""
    from app.services.chat_intents import INTENT_NAMES
    if rating not in (1, -1):
        raise ValueError("rating must be 1 or -1")
    if wanted_intent and wanted_intent != "none" and wanted_intent not in INTENT_NAMES:
        raise ValueError(f"unknown intent '{wanted_intent}'")
    learned: List[str] = []
    with _cursor() as cur:
        cur.execute(f"SELECT question, route, route_how FROM {SCHEMA}.chat_log WHERE id = ?", log_id)
        row = cur.fetchone()
        if not row:
            raise LookupError(f"chat_log id {log_id} not found")
        question, route, how = row[0], row[1], row[2]
        cur.execute(
            f"""UPDATE {SCHEMA}.chat_log SET rating = ?, rated_at = SYSUTCDATETIME(),
                    wanted_intent = COALESCE(?, wanted_intent), comment = COALESCE(?, comment)
                WHERE id = ?""",
            rating, wanted_intent, (comment or None) and comment[:1000], log_id,
        )
        routed = route in INTENT_NAMES
        if rating == 1 and routed and how != "embedding":
            _add_example(cur, question, route, 1, "thumbs_up", log_id)
            learned.append(f"+{route}")
        # A bare 👎 is only recorded: it often means "the data looks off", not "wrong
        # kind of answer". The router unlearns a match only when the user says the
        # TYPE was wrong — by picking another type, or "none of these".
        if rating == -1 and routed and wanted_intent and wanted_intent != route:
            _add_example(cur, question, route, -1, "thumbs_down", log_id)
            learned.append(f"-{route}")
        if wanted_intent and wanted_intent != "none":
            _add_example(cur, question, wanted_intent, 1, "user_pick", log_id)
            learned.append(f"+{wanted_intent}")
    if learned:
        _learned["expires"] = 0.0  # this process picks it up on the next question
        logger.info("chat feedback %s on #%d (%s): learned %s", rating, log_id, route, ", ".join(learned))
    return {"id": log_id, "rating": rating, "learned": learned}


def learned_examples() -> List[Dict[str, Any]]:
    """Active learned examples [{question, intent, polarity}], cached per process."""
    if time.monotonic() < _learned["expires"]:
        return _learned["rows"]
    try:
        with _cursor() as cur:
            cur.execute(f"SELECT question, intent, polarity FROM {SCHEMA}.chat_intent_examples "
                        f"WHERE active = 1 ORDER BY id")
            rows = [{"question": r[0], "intent": r[1], "polarity": int(r[2])} for r in cur.fetchall()]
        _learned.update(rows=rows, expires=time.monotonic() + _LEARNED_TTL)
    except Exception as e:
        logger.warning("learned examples unavailable: %s", type(e).__name__)
        _learned["expires"] = time.monotonic() + 30
    return _learned["rows"]


def review(limit: int = 50, only: str = "all") -> List[Dict[str, Any]]:
    """Recent questions for review: only = all | down | fallthrough | unrated-fallthrough."""
    where = {"down": "rating = -1", "fallthrough": "route = 'sql'",
             "unrated-fallthrough": "route = 'sql' AND rating IS NULL"}.get(only, "1 = 1")
    with _cursor() as cur:
        cur.execute(
            f"""SELECT TOP {int(min(max(limit, 1), 500))} id, asked_at, question, route, route_how,
                       route_score, row_count, duration_ms, error, rating, wanted_intent, comment
                FROM {SCHEMA}.chat_log WHERE {where} ORDER BY id DESC""")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def list_examples() -> List[Dict[str, Any]]:
    with _cursor() as cur:
        cur.execute(f"SELECT id, question, intent, polarity, source, chat_log_id, created_at "
                    f"FROM {SCHEMA}.chat_intent_examples WHERE active = 1 ORDER BY id DESC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def deactivate_example(example_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"UPDATE {SCHEMA}.chat_intent_examples SET active = 0 WHERE id = ? AND active = 1",
                    example_id)
        changed = cur.rowcount > 0
    _learned["expires"] = 0.0
    return changed
