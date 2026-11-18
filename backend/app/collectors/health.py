"""
Unified storage health status.

One canonical vocabulary for every vendor's array health, so the UI shows a
single, accurate category instead of each vendor's raw string:

    healthy   (green)  — reachable and every hardware/system check passed
    warning   (yellow) — a degraded component (failed drive, failover, fan…)
    critical  (red)    — a failed/blocked controller, node down, critical fault
    offline   (gray)   — no fresh data (array unreachable or collection stalled);
                         replaces the old, meaningless "unknown"

How it is decided:

  1. freshness — no successful health check for `offline_after` seconds (or
     never collected) => offline. When a collector can't authenticate or reach
     the array it never records a check, so the timestamp goes stale on its own.
     The timestamp is health_checked_at on the DATABASE clock, not collected_at:
     collectors run in different timezones (on-prem MST, cloud pods UTC) and
     write naive local times, and an older collector version may still be
     writing the same rows.
  2. component state — each vendor's metrics collector rolls up its documented
     CURRENT-STATE endpoints (controllers, nodes, hardware, drives, HA…) into a
     canonical array_status + a short health_detail (see health_rules.py).

Alerts are deliberately NOT an input. Four vendors' alert feeds are event logs
(NetApp EMS 24h, HPE eventlog, Hitachi SIM log, Dell alert history), so a fault
that was fixed would keep the badge red for days. Alerts stay on the Alerts page
and in the per-array alert count; health reflects what the hardware says now.

This module is pure (no DB / no I/O); callers pass the already-read values in.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

_CLOCK_SKEW_S = 300   # tolerated clock skew between collector hosts (seconds)

# ── canonical states ───────────────────────────────────────────────────────────
HEALTHY = "healthy"
WARNING = "warning"
CRITICAL = "critical"
OFFLINE = "offline"

ALL_STATES = (HEALTHY, WARNING, CRITICAL, OFFLINE)

# Severity rank among the "has data" states (offline is handled separately — it
# means "no data", not "worse than critical").
_RANK = {HEALTHY: 1, WARNING: 2, CRITICAL: 3}

# Map a vendor's raw status token -> canonical severity. Covers every string the
# collectors write today (healthy/degraded/critical/unknown) plus the common
# synonyms vendors use, so a new vendor string fails safe rather than mislabels.
_RAW_STATUS = {
    # healthy
    "ok": HEALTHY, "okay": HEALTHY, "normal": HEALTHY, "healthy": HEALTHY,
    "good": HEALTHY, "online": HEALTHY, "ready": HEALTHY, "running": HEALTHY,
    "up": HEALTHY, "active": HEALTHY, "enabled": HEALTHY, "true": HEALTHY,
    "connected": HEALTHY, "optimal": HEALTHY, "nominal": HEALTHY,
    # warning
    "warn": WARNING, "warning": WARNING, "degraded": WARNING, "minor": WARNING,
    "partial": WARNING, "partially": WARNING, "noncritical": WARNING,
    "non-critical": WARNING, "suboptimal": WARNING, "rebuilding": WARNING,
    # critical
    "critical": CRITICAL, "major": CRITICAL, "fatal": CRITICAL, "error": CRITICAL,
    "failed": CRITICAL, "failure": CRITICAL, "fault": CRITICAL, "down": CRITICAL,
    "offline": CRITICAL, "unreachable": CRITICAL, "severe": CRITICAL,
    "disconnected": CRITICAL, "dead": CRITICAL,
    # Hitachi VSP / component states: a controller/drive reports "Blocked" when it
    # is fenced out (down) and "Error" when it has failed — both are critical.
    "blocked": CRITICAL, "blockage": CRITICAL,
}


def normalize_status(raw: Optional[str]) -> Optional[str]:
    """Vendor raw status string -> canonical severity, or None if unknown/blank.

    None means "no usable signal" (unknown / n/a / empty) — NOT offline. Offline
    is decided from freshness, not from a vendor writing the literal "unknown".
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in ("unknown", "n/a", "na", "none", "null", "-"):
        return None
    return _RAW_STATUS.get(s)


def _worse(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Return the more severe of two canonical severities (ignoring None)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _RANK[a] >= _RANK[b] else b


def _parse_dt(value) -> Optional[datetime]:
    """Parse a datetime from a datetime object or an ISO-ish string. Returns a
    naive datetime (the collectors write local-time naive timestamps)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    s = str(value).strip().replace("T", " ")
    # drop timezone suffix and sub-seconds: "2026-11-17 08:30:01.123+00" -> "2026-11-17 08:30:01"
    s = s.split("+")[0].split(".")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def age_seconds(collected_at) -> Optional[float]:
    """Seconds since `collected_at`, or None if it can't be parsed.

    collected_at is a NAIVE timestamp in the WRITER's local timezone, and
    collectors run in different zones (on-prem containers are MST, cloud pods
    UTC). Read it against both the local and the UTC clock, drop a reading that
    lands in the future (beyond a few minutes of skew — implausible), and take
    the most recent plausible one. Only used for legacy rows — rows the current
    collector has checked carry health_checked_at on the DB clock.
    """
    dt = _parse_dt(collected_at)
    if dt is None:
        return None
    candidates = ((datetime.now() - dt).total_seconds(),
                  (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds())
    plausible = [c for c in candidates if c >= -_CLOCK_SKEW_S]
    age = min(plausible) if plausible else min(candidates, key=abs)
    return max(age, 0.0)


def _age_label(age: float) -> str:
    mins = int(age // 60)
    return f"{mins} min" if mins < 120 else f"{mins // 60} h"


def evaluate(
    array_status: Optional[str] = None,
    controller_status: Optional[str] = None,
    collected_at=None,
    health_detail: Optional[str] = None,
    offline_after: int = 1800,
    health_state: Optional[str] = None,
    health_checked_at=None,
    db_utc_now=None,
) -> Tuple[str, str]:
    """Compute the canonical (state, reason) for one array at read time.

    Preferred path: health_state + health_checked_at, written only by the
    current collector (base._write_health) and timed on the DATABASE clock
    (SYSUTCDATETIME), compared with `db_utc_now` from the same clock — immune to
    collector timezones and to older collector versions that still overwrite
    array_status/collected_at on the same rows.

    Fallback (row never checked by the current collector): array_status /
    controller_status + a timezone-tolerant age of collected_at.

    `offline_after` is the staleness threshold in seconds (0 disables the check).
    `reason` is a short human string for the status tooltip / drilldown.
    """
    detail = (health_detail or "").strip()

    checked, now = _parse_dt(health_checked_at), _parse_dt(db_utc_now)
    if checked is not None and now is not None:
        age = max((now - checked).total_seconds(), 0.0)
        if offline_after and age > offline_after:
            return OFFLINE, f"no successful health check for {_age_label(age)} — array unreachable or collection failing"
        state = normalize_status(health_state)
        if state is None:
            return HEALTHY, detail or "reachable; no hardware health signal from this array"
        return state, detail or ("all hardware checks passed" if state == HEALTHY else f"vendor reports {state}")

    # Legacy row: freshness from collected_at, status from array/controller_status.
    if collected_at is None:
        return OFFLINE, "never reported — collection is failing for this array"
    age = age_seconds(collected_at)
    if age is None:
        return OFFLINE, "unreadable collection timestamp"
    if offline_after and age > offline_after:
        return OFFLINE, f"no data for {_age_label(age)} — array unreachable or collection failing"

    state = _worse(normalize_status(array_status), normalize_status(controller_status))
    if state is None:
        return HEALTHY, detail or "reachable; no hardware health signal from this array"
    return state, detail or ("all hardware checks passed" if state == HEALTHY else f"vendor reports {state}")


class HealthRollup:
    """Accumulates one array's component findings into (state, detail).

    Collectors call check() for every area they actually inspected and
    warn()/crit() for each problem found. result() returns:
      * (None, "")                  — nothing could be checked (no signal)
      * ("healthy", "OK: a, b")     — every inspected area was clean
      * ("warning"|"critical", "…") — worst finding, problems listed worst-first
    """

    def __init__(self):
        self.areas: list = []
        self._crit: list = []
        self._warn: list = []

    def check(self, area: str) -> None:
        if area not in self.areas:
            self.areas.append(area)

    def warn(self, msg: str) -> None:
        self._warn.append(msg)

    def crit(self, msg: str) -> None:
        self._crit.append(msg)

    def add(self, severity: Optional[str], msg: str) -> None:
        if severity == CRITICAL:
            self.crit(msg)
        elif severity == WARNING:
            self.warn(msg)

    def result(self, max_len: int = 500) -> Tuple[Optional[str], str]:
        if self._crit:
            state = CRITICAL
        elif self._warn:
            state = WARNING
        elif self.areas:
            return HEALTHY, ("OK: " + ", ".join(self.areas))[:max_len]
        else:
            return None, ""
        detail = "; ".join(self._crit + self._warn)
        return state, (detail if len(detail) <= max_len else detail[: max_len - 1] + "…")
