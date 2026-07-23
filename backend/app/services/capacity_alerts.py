"""
Capacity alerting service.

Two kinds of checks, both persisted into the existing USM.messages table
(so they show up in the unified Alerts feed alongside vendor alerts) and both
notified via the existing Teams webhook:

  1. Per-array utilization threshold crossings (default 80/90/95%).
     Only the HIGHEST currently-exceeded threshold is active per array — e.g.
     an array at 92% shows one open alert for the 90% threshold, not two.
     Auto-resolves (closed + resolved=1) once utilization drops back below.

  2. Fleet-wide "projected full" — using the same growth projection that
     powers the Capacity page (app/services/capacity_projection.py). Fires
     when the fleet is trending toward full within CAPACITY_PROJECTED_FULL_DAYS.

Both re-notify at most once every CAPACITY_ALERT_RESEND_DAYS while the
condition persists, to avoid spamming Teams on every scheduler tick.

Synthetic message_id ranges (kept out of vendor-native ID space):
    900000 + threshold   -> per-array threshold alerts (e.g. 900080, 900090, 900095)
    999001                -> fleet projected-full alert (array_name = "FLEET")
"""

import logging
from datetime import datetime

from app.db.session import get_db_cursor, rows_to_dicts
from app.core.config import get_settings
from app.services.notification import send_teams_alert
from app.services.capacity_projection import compute_daily_trend

logger = logging.getLogger("usm.capacity_alerts")
settings = get_settings()
SCHEMA = settings.db_schema

FLEET_PSEUDO_ARRAY = "FLEET"
FLEET_MESSAGE_ID = 999001
THRESHOLD_ID_BASE = 900000


def _parse_thresholds() -> list:
    out = []
    for tok in (settings.capacity_alert_thresholds or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return sorted(out)


def _severity_for_threshold(threshold: float) -> str:
    return "critical" if threshold >= 90 else "warning"


def _existing_row(cursor, array_name: str, message_id: int) -> dict | None:
    cursor.execute(
        f"SELECT id, teams_notified, resolved FROM {SCHEMA}.messages "
        f"WHERE array_name=? AND message_id=?",
        (array_name, message_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _should_notify(existing: dict | None) -> bool:
    """Notify if new, or if previously notified more than resend-days ago."""
    if not existing:
        return True
    last = existing.get("teams_notified")
    if not last:
        return True
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last)
        except ValueError:
            return True
    days_since = (datetime.now() - last).total_seconds() / 86400.0
    return days_since >= settings.capacity_alert_resend_days


def _upsert_alert(
    cursor,
    array_name: str,
    vendor: str,
    message_id: int,
    severity: str,
    event: str,
    component_type: str,
    component_name: str,
    expected: str,
    actual: str,
) -> dict:
    """Insert or update an open alert row. Returns the row dict (post-write)."""
    now = datetime.now().isoformat()
    existing = _existing_row(cursor, array_name, message_id)

    if existing:
        cursor.execute(
            f"UPDATE {SCHEMA}.messages SET "
            f"  severity=?, event=?, actual=?, collected_at=?, resolved=0, closed=NULL, "
            # On a genuine resolved->open transition, clear teams_notified so the
            # re-fired alert notifies again (otherwise a flapping capacity alert
            # goes silent for up to the resend window). CASE reads the pre-update
            # value, so an already-open row keeps its notified timestamp.
            f"  teams_notified = CASE WHEN resolved=1 THEN NULL ELSE teams_notified END "
            f"WHERE array_name=? AND message_id=?",
            (severity, event, actual, now, array_name, message_id),
        )
    else:
        cursor.execute(
            f"INSERT INTO {SCHEMA}.messages "
            f"  (array_name, vendor, message_id, event, severity, component_type, "
            f"   component_name, opened, expected, actual, collected_at, resolved) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (array_name, vendor, message_id, event, severity, component_type,
             component_name, now, expected, actual, now),
        )
    return existing or {}


def _resolve_alert(cursor, array_name: str, message_id: int) -> bool:
    """Close/resolve an alert if it's currently open. Returns True if it changed state."""
    cursor.execute(
        f"SELECT resolved FROM {SCHEMA}.messages WHERE array_name=? AND message_id=?",
        (array_name, message_id),
    )
    row = cursor.fetchone()
    if not row or row[0]:
        return False  # doesn't exist or already resolved
    cursor.execute(
        f"UPDATE {SCHEMA}.messages SET resolved=1, closed=? "
        f"WHERE array_name=? AND message_id=?",
        (datetime.now().isoformat(), array_name, message_id),
    )
    return True


def _mark_notified(cursor, array_name: str, message_id: int) -> None:
    cursor.execute(
        f"UPDATE {SCHEMA}.messages SET teams_notified=GETDATE() "
        f"WHERE array_name=? AND message_id=?",
        (array_name, message_id),
    )


# ---------------------------------------------------------------------------
# Per-array utilization threshold checks
# ---------------------------------------------------------------------------

def _fetch_array_utilization() -> list:
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                mc.array_name,
                COALESCE(ma.vendor, mc.vendor, 'unknown') AS vendor,
                mc.capacity_used_pct
            FROM {SCHEMA}.metrics_current mc WITH (NOLOCK)
            LEFT JOIN {SCHEMA}.managed_arrays ma WITH (NOLOCK)
                ON ma.array_name = mc.array_name
            WHERE mc.capacity_used_pct IS NOT NULL
            """
        )
        return rows_to_dicts(cursor, cursor.fetchall())


def check_array_thresholds() -> dict:
    """
    For every array, determine the highest exceeded threshold (if any) and
    keep exactly one open alert for it; resolve alerts for thresholds no
    longer exceeded. Sends a Teams notification for new/resent alerts.
    """
    thresholds = _parse_thresholds()
    if not thresholds:
        return {"checked": 0, "opened": 0, "resolved": 0, "notified": 0}

    rows = _fetch_array_utilization()
    opened = resolved = notified = 0

    for r in rows:
        array_name = r["array_name"]
        vendor = (r.get("vendor") or "unknown").lower()
        pct = float(r.get("capacity_used_pct") or 0)

        exceeded = [t for t in thresholds if pct >= t]
        active_threshold = max(exceeded) if exceeded else None

        with get_db_cursor() as cursor:
            for t in thresholds:
                message_id = THRESHOLD_ID_BASE + int(t)
                if t == active_threshold:
                    existing = _existing_row(cursor, array_name, message_id)
                    was_new = existing is None or bool(existing.get("resolved"))
                    _upsert_alert(
                        cursor, array_name, vendor, message_id,
                        severity=_severity_for_threshold(t),
                        event=f"Capacity utilization reached {pct:.1f}% (>= {t:.0f}% threshold)",
                        component_type="capacity",
                        component_name=f"{t:.0f}% threshold",
                        expected=f"< {t:.0f}%",
                        actual=f"{pct:.1f}%",
                    )
                    if was_new:
                        opened += 1
                    if _should_notify(existing):
                        ok = send_teams_alert(
                            array_name=array_name,
                            vendor=vendor,
                            severity=_severity_for_threshold(t),
                            event=f"Capacity utilization reached {pct:.1f}% (>= {t:.0f}% threshold)",
                            component=f"{t:.0f}% threshold",
                            message_id=str(message_id),
                        )
                        if ok:
                            _mark_notified(cursor, array_name, message_id)
                            notified += 1
                else:
                    if _resolve_alert(cursor, array_name, message_id):
                        resolved += 1

    logger.info(
        f"Capacity threshold check: {len(rows)} arrays checked, "
        f"{opened} opened/updated, {resolved} resolved, {notified} Teams notifications sent"
    )
    return {"checked": len(rows), "opened": opened, "resolved": resolved, "notified": notified}


# ---------------------------------------------------------------------------
# Fleet-wide projected-full check
# ---------------------------------------------------------------------------

def check_fleet_projection() -> dict:
    """
    Fires (or resolves) a single fleet-level alert when the growth projection
    indicates the fleet will fill within CAPACITY_PROJECTED_FULL_DAYS.
    """
    result = compute_daily_trend(settings.capacity_alert_trend_days)
    proj = result["projection"]
    days_to_full = proj.get("days_to_full")
    trend = proj.get("trend")

    should_fire = (
        trend == "growing"
        and days_to_full is not None
        and days_to_full <= settings.capacity_projected_full_days
    )

    with get_db_cursor() as cursor:
        if should_fire:
            severity = "critical" if days_to_full <= 14 else "warning"
            event = (
                f"Fleet capacity projected full in ~{days_to_full}d "
                f"(by {proj.get('projected_full_date')}) at "
                f"{proj.get('avg_rate_tb_per_day')} TB/day, "
                f"{proj.get('headroom_tb')} TB headroom"
            )
            existing = _existing_row(cursor, FLEET_PSEUDO_ARRAY, FLEET_MESSAGE_ID)
            _upsert_alert(
                cursor, FLEET_PSEUDO_ARRAY, "usm", FLEET_MESSAGE_ID,
                severity=severity,
                event=event,
                component_type="capacity_projection",
                component_name="fleet",
                expected=f">= {settings.capacity_projected_full_days}d headroom",
                actual=f"~{days_to_full}d",
            )
            notified = False
            if _should_notify(existing):
                ok = send_teams_alert(
                    array_name=FLEET_PSEUDO_ARRAY,
                    vendor="usm",
                    severity=severity,
                    event=event,
                    component="fleet capacity projection",
                    message_id=str(FLEET_MESSAGE_ID),
                )
                if ok:
                    _mark_notified(cursor, FLEET_PSEUDO_ARRAY, FLEET_MESSAGE_ID)
                    notified = True
            logger.info(f"Fleet projection alert active: {event}")
            return {"fired": True, "notified": notified, "days_to_full": days_to_full}
        else:
            was_resolved = _resolve_alert(cursor, FLEET_PSEUDO_ARRAY, FLEET_MESSAGE_ID)
            return {"fired": False, "resolved": was_resolved, "trend": trend}


def run_capacity_alert_checks() -> dict:
    """Entry point called by the scheduler (and available for manual trigger)."""
    if not settings.capacity_alerts_enabled:
        return {"enabled": False}
    thresholds_result = check_array_thresholds()
    fleet_result = check_fleet_projection()
    return {
        "enabled": True,
        "checked_at": datetime.now().isoformat(),
        "thresholds": thresholds_result,
        "fleet_projection": fleet_result,
    }
