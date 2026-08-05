"""
Shared alert-lifecycle helpers used by every vendor alert collector.

Centralizes two things that were previously missing or copy-pasted:

- resolve_absent_alerts(): absence-based closure. Vendor alert APIs (Pure, HPE,
  Hitachi, Dell, Oracle) return the array's CURRENT open/active alert set; when
  an alert clears it simply drops off. None of the collectors marked those rows
  resolved, so cleared alerts showed as active forever and USM.messages grew
  unbounded. This marks any of a vendor's open rows that are no longer in the
  current set as resolved.

- opened_recently(): a recency gate for notifications, so ingesting a backlog of
  long-open alerts (e.g. after the Pure open=true fetch fix) records/shows them
  but does not re-page. Genuinely new alerts open within a poll cycle, well
  inside the window.

NetApp is intentionally NOT a caller: its EMS events are point-in-time log
entries (no open/closed lifecycle), so it uses age-based resolution instead.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("usm.alerts.lifecycle")


def opened_recently(opened: Optional[str], max_age_hours: int) -> bool:
    """True if `opened` is within max_age_hours of now. Unknown/unparseable
    timestamps default to True (notify rather than silently drop)."""
    if not opened:
        return True
    try:
        dt = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        return age_hours <= max_age_hours
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Custom severity overrides
# ---------------------------------------------------------------------------
# Vendors report a severity, but it isn't always how the team wants to treat the
# event (e.g. Pure reports a controller reboot as "warning"; we want "critical").
# Rules are stored as a JSON list in app_settings['alert_severity_overrides'] and
# applied to each alert message BEFORE save/notify, so both the stored severity and
# any Datadog/Teams paging reflect the override. Rule shape:
#   {"vendor": "pure"|"", "field": "event"|"component_type"|"component_name"|"any",
#    "op": "contains"|"equals"|"startswith"|"regex", "value": "reboot",
#    "severity": "critical"|"warning"|"info", "enabled": true, "note": "..."}
_override_cache: dict = {"rules": None, "ts": 0.0}


def _load_severity_overrides(schema: str) -> list:
    """Load override rules from app_settings (JSON), cached 60s."""
    import time
    now = time.monotonic()
    if _override_cache["rules"] is not None and (now - _override_cache["ts"]) < 60:
        return _override_cache["rules"]
    import json
    from app.db.session import get_db_cursor
    rules: list = []
    try:
        with get_db_cursor() as cur:
            cur.execute(
                f"SELECT setting_value FROM {schema}.app_settings WITH (NOLOCK) "
                f"WHERE setting_key='alert_severity_overrides'"
            )
            row = cur.fetchone()
        if row and row[0]:
            parsed = json.loads(row[0])
            if isinstance(parsed, list):
                rules = parsed
        _override_cache.update(rules=rules, ts=now)
    except Exception as e:
        logger.warning(f"severity-override load failed (non-fatal): {e}")
        return _override_cache["rules"] or []
    return rules


def _rule_matches(rule: dict, msg: dict) -> bool:
    val = (rule.get("value") or "").lower()
    if not val:
        return False
    field = (rule.get("field") or "event").lower()
    if field == "any":
        hay = " ".join(str(msg.get(k) or "") for k in
                       ("event", "component_type", "component_name", "actual")).lower()
    else:
        hay = str(msg.get(field) or "").lower()
    op = (rule.get("op") or "contains").lower()
    if op == "equals":
        return hay == val
    if op == "startswith":
        return hay.startswith(val)
    if op == "regex":
        import re
        try:
            return re.search(val, hay) is not None
        except re.error:
            return False
    return val in hay  # default: contains


def apply_severity_overrides(messages: list, schema: Optional[str] = None) -> int:
    """Rewrite msg['severity'] for any message matching a configured rule. First
    matching (enabled) rule wins. Returns the number of messages changed. Non-fatal."""
    if not messages:
        return 0
    if schema is None:
        from app.core.config import get_settings
        schema = get_settings().db_schema
    rules = _load_severity_overrides(schema)
    if not rules:
        return 0
    changed = 0
    for msg in messages:
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            rv = (rule.get("vendor") or "").lower()
            if rv and rv != (msg.get("vendor") or "").lower():
                continue
            if _rule_matches(rule, msg):
                new_sev = (rule.get("severity") or "").lower()
                if new_sev and new_sev != (msg.get("severity") or "").lower():
                    msg["severity"] = new_sev
                    changed += 1
                break  # first matching rule wins for this message
    return changed


def resolve_absent_alerts(cursor, schema: str, array_name: str, vendor: str,
                          current_ids: List) -> int:
    """Mark <vendor> alerts for <array_name> resolved when they are no longer in
    the array's current open set (current_ids). Returns the number resolved.

    CALLER CONTRACT: only call this when the alert fetch SUCCEEDED. Calling it
    after a failed/empty fetch would resolve every open alert for the array.
    """
    now = datetime.now().isoformat()
    closed_expr = "CASE WHEN closed IS NULL OR closed='' THEN ? ELSE closed END"
    ids = [i for i in current_ids if i is not None]
    if ids:
        placeholders = ",".join("?" * len(ids))
        cursor.execute(
            f"""UPDATE {schema}.messages
                SET resolved=1, closed={closed_expr}
                WHERE array_name=? AND vendor=? AND resolved=0
                  AND message_id NOT IN ({placeholders})""",
            (now, array_name, vendor, *ids),
        )
    else:
        # Array reports zero open alerts -> resolve all our open rows for it.
        cursor.execute(
            f"""UPDATE {schema}.messages
                SET resolved=1, closed={closed_expr}
                WHERE array_name=? AND vendor=? AND resolved=0""",
            (now, array_name, vendor),
        )
    n = cursor.rowcount or 0
    if n > 0:
        logger.info(f"[{array_name}] resolved {n} {vendor} alerts no longer open on the array")
    return n


# Cache of array_name -> group_label so the Datadog group gate doesn't hit the DB
# on every notification. Groups change rarely; refresh every 5 minutes.
_group_cache: dict = {}
_group_cache_ts: float = 0.0


def _array_groups(schema: str) -> dict:
    """array_name -> group_label (lowercased), cached for 5 min."""
    global _group_cache, _group_cache_ts
    import time
    now = time.monotonic()
    if _group_cache and (now - _group_cache_ts) < 300:
        return _group_cache
    from app.db.session import get_db_cursor
    m: dict = {}
    try:
        with get_db_cursor() as cur:
            cur.execute(f"SELECT array_name, group_label FROM {schema}.managed_arrays WITH (NOLOCK)")
            for name, grp in cur.fetchall():
                m[name] = (grp or "").lower()
        _group_cache = m
        _group_cache_ts = now
    except Exception as e:
        logger.warning(f"array-group lookup for Datadog gate failed (non-fatal): {e}")
        return _group_cache or {}
    return m


def _datadog_group_allowed(schema: str, array_name: str, allowed_prefixes: list) -> bool:
    """True if this array's group_label starts with one of the allowed prefixes.
    Empty allow-list means all groups are allowed."""
    if not allowed_prefixes:
        return True
    grp = _array_groups(schema).get(array_name, "")
    return any(grp.startswith(p) for p in allowed_prefixes)


# Runtime paging toggle + high-water mark, read from the app_settings table so a
# UI toggle takes effect in the collector process (a SEPARATE container) without a
# redeploy. Cached briefly so the gate doesn't hit the DB on every alert.
_paging_cache: dict = {"enabled": None, "since": None, "ts": 0.0}


def _paging_runtime(schema: str):
    """Return (enabled: bool, since_iso: Optional[str]) from app_settings, cached 30s.
    Defaults to enabled + no high-water when the settings are absent (preserves
    prior behavior until someone toggles the switch)."""
    import time
    now = time.monotonic()
    if _paging_cache["ts"] and (now - _paging_cache["ts"]) < 30:
        return _paging_cache["enabled"], _paging_cache["since"]
    from app.db.session import get_db_cursor
    enabled, since = True, None
    try:
        with get_db_cursor() as cur:
            cur.execute(
                f"SELECT setting_key, setting_value FROM {schema}.app_settings WITH (NOLOCK) "
                f"WHERE setting_key IN ('datadog_paging_enabled','datadog_paging_since')"
            )
            d = {k: v for k, v in cur.fetchall()}
        if "datadog_paging_enabled" in d:
            enabled = str(d["datadog_paging_enabled"]).strip().lower() in ("1", "true", "yes", "on")
        since = d.get("datadog_paging_since")
        _paging_cache.update(enabled=enabled, since=since, ts=now)
    except Exception as e:
        logger.warning(f"datadog paging runtime read failed (non-fatal): {e}")
        if _paging_cache["enabled"] is not None:
            return _paging_cache["enabled"], _paging_cache["since"]
    return enabled, since


def _opened_after(opened, since_iso) -> bool:
    """True if the alert's opened time is at/after the paging high-water mark.
    No high-water set, or unparseable timestamps → True (don't silently drop)."""
    if not since_iso or not opened:
        return True
    try:
        from datetime import datetime, timezone
        o = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
        s = datetime.fromisoformat(str(since_iso).replace("Z", "+00:00"))
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        return o >= s
    except Exception:
        return True


def dispatch_datadog_new(schema: str, vendor: str, alerts: List[dict]) -> int:
    """Open a Datadog event for each new critical/warning alert and stamp
    datadog_notified so the resolver can later close it and it isn't re-opened.

    `alerts` is the collector's list of new message dicts. send_datadog_alert()
    self-filters on severity + datadog_enabled, so passing the full new-alert list
    is safe (info-level and disabled → no-op). Non-fatal per alert.

    Datadog paging is gated by, in order:
      1. env DATADOG_ENABLED (hard master — integration configured at all),
      2. the runtime on/off switch (app_settings 'datadog_paging_enabled', toggled
         from the UI without a redeploy),
      3. the high-water mark (app_settings 'datadog_paging_since') — only alerts
         opened at/after the switch was turned on page, so enabling never backfills,
      4. the group allow-list (settings.datadog_notify_groups, e.g. "Cloud-AZU"),
      5. per-alert idempotency (skip if it already carries a Datadog event) — so a
         restart never re-pages.
    All of this is Datadog-ONLY; Teams notifications happen separately in the
    collector and are unaffected.
    """
    from app.core.config import get_settings
    settings = get_settings()
    if not settings.datadog_enabled or not alerts:
        return 0
    enabled, since = _paging_runtime(schema)
    if not enabled:
        return 0
    from app.services.notification import send_datadog_alert
    from app.db.session import get_db_cursor

    allowed = [g.strip().lower() for g in (settings.datadog_notify_groups or "").split(",") if g.strip()]
    allowed_vendors = [v.strip().lower() for v in (settings.datadog_notify_vendors or "").split(",") if v.strip()]

    n = 0
    for alert in alerts:
        alert.setdefault("vendor", vendor)
        arr = alert.get("array_name")
        mid = alert.get("message_id")
        # Group gate — skip Datadog for arrays outside the allowed groups.
        if not _datadog_group_allowed(schema, arr, allowed):
            continue
        # Vendor gate — skip Datadog for vendors outside the allow-list (e.g. keep
        # Azure Pure but drop Azure NetApp CVO, which share the Cloud-AZU group).
        if allowed_vendors and (alert.get("vendor") or "").lower() not in allowed_vendors:
            continue
        # High-water — only page alerts opened at/after the switch was turned on.
        if not _opened_after(alert.get("opened"), since):
            continue
        # Idempotency — never re-page an alert that already has a Datadog event
        # (covers restarts and the Teams-disabled case).
        try:
            with get_db_cursor() as cur:
                cur.execute(
                    f"SELECT datadog_notified FROM {schema}.messages WITH (NOLOCK) "
                    f"WHERE array_name=? AND message_id=?", (arr, mid),
                )
                row = cur.fetchone()
            if row and row[0]:
                continue
        except Exception:
            pass
        try:
            res = send_datadog_alert(alert)
            if res is not None:
                event_id = (res.get("id") or res.get("uid") or "")[:120]
                event_url = (res.get("url") or "")[:300]
                with get_db_cursor() as cur:
                    cur.execute(
                        f"UPDATE {schema}.messages SET datadog_notified=GETDATE(), "
                        f"datadog_event_id=?, datadog_event_url=? "
                        f"WHERE array_name=? AND message_id=?",
                        (event_id, event_url, arr, mid),
                    )
                n += 1
        except Exception as e:
            logger.warning(f"[{arr}] datadog open failed (non-fatal): {e}")
    return n


def push_datadog_resolutions(schema: str, array_name: str, vendor: str) -> int:
    """Send a Datadog 'ok' (resolve) event for every alert of <array_name>/<vendor>
    that USM has marked resolved but still carries an open Datadog event
    (datadog_notified IS NOT NULL). On a successful resolve post, clear the flag so
    it is never pushed again. Returns the number resolved in Datadog.

    Call this AFTER resolution has been committed (its own short cursors, so a slow
    Datadog POST never holds the collector's transaction open). No-op unless Datadog
    is enabled. Non-fatal — a failed post just leaves the flag set to retry next cycle.
    """
    from app.core.config import get_settings
    if not get_settings().datadog_enabled:
        return 0
    from app.services.notification import resolve_datadog_alert
    from app.db.session import get_db_cursor

    try:
        with get_db_cursor() as cur:
            cur.execute(
                f"""SELECT message_id, event FROM {schema}.messages WITH (NOLOCK)
                    WHERE array_name=? AND vendor=? AND resolved=1
                      AND datadog_notified IS NOT NULL""",
                (array_name, vendor),
            )
            pending = cur.fetchall()
    except Exception as e:
        logger.warning(f"[{array_name}] datadog-resolve query failed (non-fatal): {e}")
        return 0

    n = 0
    for message_id, event in pending:
        try:
            if resolve_datadog_alert(vendor, array_name, message_id, event or ""):
                with get_db_cursor() as cur:
                    cur.execute(
                        f"UPDATE {schema}.messages SET datadog_notified=NULL "
                        f"WHERE array_name=? AND vendor=? AND message_id=?",
                        (array_name, vendor, message_id),
                    )
                n += 1
        except Exception as e:
            logger.warning(f"[{array_name}] datadog-resolve for {message_id} failed (non-fatal): {e}")
    if n:
        logger.info(f"[{array_name}] resolved {n} {vendor} alerts in Datadog")
    return n
