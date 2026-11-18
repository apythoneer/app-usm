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

  1. freshness — metrics_current.collected_at older than `offline_after`
     seconds (or never collected) => offline. When a collector can't
     authenticate or reach the array it never writes a new row, so collected_at
     goes stale on its own.
  2. component state — each vendor's metrics collector rolls up its documented
     CURRENT-STATE endpoints (controllers, nodes, hardware, drives, HA…) into a
     canonical array_status + a short health_detail (see health_rules.py).

Alerts are deliberately NOT an input. Four vendors' alert feeds are event logs
(NetApp EMS 24h, HPE eventlog, Hitachi SIM log, Dell alert history), so a fault
that was fixed would keep the badge red for days. Alerts stay on the Alerts page
and in the per-array alert count; health reflects what the hardware says now.

This module is pure (no DB / no I/O); callers pass the already-read values in.
"""

from datetime import datetime
from typing import Optional, Tuple

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
    """Seconds since `collected_at`, or None if it can't be parsed."""
    dt = _parse_dt(collected_at)
    if dt is None:
        return None
    return (datetime.now() - dt).total_seconds()


def evaluate(
    array_status: Optional[str] = None,
    controller_status: Optional[str] = None,
    collected_at=None,
    health_detail: Optional[str] = None,
    offline_after: int = 1800,
) -> Tuple[str, str]:
    """Compute the canonical (state, reason) for one array at read time.

    `offline_after` is the staleness threshold in seconds (0 disables the check).
    `reason` is a short human string for the status tooltip / drilldown — the
    collector's health_detail when it has one.
    """
    # 1. Freshness → offline. A stalled/unreachable collector stops advancing
    #    collected_at, so this is how "unknown" becomes a real "offline".
    if collected_at is None:
        return OFFLINE, "never reported — collection is failing for this array"
    age = age_seconds(collected_at)
    if age is None:
        return OFFLINE, "unreadable collection timestamp"
    if offline_after and age > offline_after:
        mins = int(age // 60)
        label = f"{mins} min" if mins < 120 else f"{mins // 60} h"
        return OFFLINE, f"no data for {label} — array unreachable or collection failing"

    # 2. Component state written by the collector (worst of array/controller).
    state = _worse(normalize_status(array_status), normalize_status(controller_status))
    detail = (health_detail or "").strip()
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
