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


def dispatch_datadog_new(schema: str, vendor: str, alerts: List[dict]) -> int:
    """Open a Datadog event for each new critical/warning alert and stamp
    datadog_notified so the resolver can later close it and it isn't re-opened.

    `alerts` is the collector's list of new message dicts. send_datadog_alert()
    self-filters on severity + datadog_enabled, so passing the full new-alert list
    is safe (info-level and disabled → no-op). Non-fatal per alert.
    """
    from app.core.config import get_settings
    if not get_settings().datadog_enabled or not alerts:
        return 0
    from app.services.notification import send_datadog_alert
    from app.db.session import get_db_cursor

    n = 0
    for alert in alerts:
        alert.setdefault("vendor", vendor)
        try:
            if send_datadog_alert(alert):
                with get_db_cursor() as cur:
                    cur.execute(
                        f"UPDATE {schema}.messages SET datadog_notified=GETDATE() "
                        f"WHERE array_name=? AND message_id=?",
                        (alert.get("array_name"), alert.get("message_id")),
                    )
                n += 1
        except Exception as e:
            logger.warning(f"[{alert.get('array_name')}] datadog open failed (non-fatal): {e}")
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
