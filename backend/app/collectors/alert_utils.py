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
