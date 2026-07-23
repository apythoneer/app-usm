"""
Hitachi VSP Alert Collector — collects alerts from Configuration Manager REST API.
Auto-registered with CollectorRegistry.

Note: The VSP REST API alerts endpoint may return HTTP 400 on some firmware versions.
In that case, we log a warning and return empty alerts (no critical failure).
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.hitachi.client import HitachiVSPClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.collectors.alert_utils import opened_recently, resolve_absent_alerts
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.hitachi.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}

# VSP errorLevel -> USM severity (per the CM REST alerts doc):
#   Acute/Serious = failures, Moderate = warning, Service = informational.
_SEVERITY_MAP = {
    "Acute": "critical",
    "Serious": "critical",
    "Moderate": "warning",
    "Service": "info",
}

# The alerts resource is per-location; query all three. DKC allows count up to
# 10240, CTL1/CTL2 up to 256. Alerts come back newest-first.
_ALERT_TYPES = (("DKC", 1024), ("CTL1", 256), ("CTL2", 256))


@CollectorRegistry.register("hitachi", "alerts")
class HitachiAlertsCollector(BaseCollector):
    VENDOR = "hitachi"
    COLLECTOR_TYPE = "alerts"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"Hitachi array '{array_config.name}' has no cred_key")
        self.client = HitachiVSPClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        messages = []
        any_ok = False
        # SIM alerts are stored per location; the endpoint REQUIRES ?type= and
        # defaults to only 10 rows. Query DKC/CTL1/CTL2 with a high count. A 404
        # means "no alerts of that type" (valid empty), not an error. (The old code
        # called bare `alerts` -> HTTP 400 and collected nothing.)
        base = f"{self.client.base_url}/storages/{self.client.storage_device_id}/alerts"
        cutoff = datetime.now() - timedelta(days=max(settings.hitachi_alert_lookback_days, 1))
        for typ, count in _ALERT_TYPES:
            try:
                resp = self.client.session.get(base, params={"type": typ, "count": count}, timeout=30)
            except Exception as e:
                logger.warning(f"[{self.array_name}] alerts type={typ} error: {e}")
                continue
            if resp.status_code == 404:
                any_ok = True
                continue
            if resp.status_code != 200:
                logger.warning(f"[{self.array_name}] alerts type={typ} -> HTTP {resp.status_code}")
                continue
            any_ok = True
            for a in resp.json().get("data", []):
                opened = a.get("occurenceTime", "") or ""
                # SIM alerts accumulate for years; keep only the recent window.
                try:
                    if opened and datetime.fromisoformat(opened) < cutoff:
                        continue
                except ValueError:
                    pass
                idx = a.get("alertIndex") or str(a.get("alertId", ""))
                loc = a.get("location") or ""
                messages.append({
                    "array_name": self.array_name,
                    "vendor": "hitachi",
                    # Stable id from the globally-unique alertIndex (hashlib, so it
                    # does not depend on PYTHONHASHSEED like the old hash()). Modulo
                    # keeps it within the INT message_id column (2^31-1); an 8-hex
                    # md5 slice can exceed that and overflow on INSERT.
                    "message_id": int(hashlib.md5(str(idx).encode()).hexdigest()[:8], 16) % 2147483647,
                    "event": (a.get("errorDetail") or a.get("errorSection") or "")[:500],
                    "severity": _SEVERITY_MAP.get(a.get("errorLevel", ""), "info"),
                    "component_type": (a.get("errorSection") or "")[:100],
                    "component_name": (f"{typ}:{loc}" if loc and loc != "-" else typ)[:255],
                    "opened": opened,
                    "closed": "",
                    "expected": "",
                    "actual": str(a.get("referenceCode", "")),
                    "collected_at": datetime.now().isoformat(),
                })

        logger.info(f"[{self.array_name}] {len(messages)} alerts collected (SIM, last {settings.hitachi_alert_lookback_days}d)")
        return {"messages": messages, "fetch_ok": any_ok}

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        messages = data.get("messages", [])
        new_alerts: List[Dict] = []

        try:
            with get_db_cursor() as cursor:
                for msg in messages:
                    cursor.execute(
                        f"SELECT id, alerted, teams_notified "
                        f"FROM {SCHEMA}.messages WHERE array_name=? AND message_id=?",
                        (msg["array_name"], msg["message_id"]),
                    )
                    existing = cursor.fetchone()

                    if existing:
                        cursor.execute(
                            f"""UPDATE {SCHEMA}.messages SET
                                event=?, severity=?, component_type=?, component_name=?,
                                opened=?, collected_at=?, resolved=0
                            WHERE array_name=? AND message_id=?""",
                            (msg["event"], msg["severity"], msg["component_type"],
                             msg["component_name"], msg["opened"], msg["collected_at"],
                             msg["array_name"], msg["message_id"]),
                        )
                        if not existing[1] and not existing[2] and msg["severity"] in ALERT_SEVERITIES and opened_recently(msg["opened"], settings.alert_notify_max_age_hours):
                            new_alerts.append(msg)
                    else:
                        cursor.execute(
                            f"""INSERT INTO {SCHEMA}.messages (
                                array_name, vendor, message_id, event, severity,
                                component_type, component_name, opened, closed,
                                expected, actual, collected_at, suppressed, resolved
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                            (msg["array_name"], "hitachi", msg["message_id"], msg["event"],
                             msg["severity"], msg["component_type"], msg["component_name"],
                             msg["opened"], msg["closed"], msg["expected"], msg["actual"],
                             msg["collected_at"]),
                        )
                        if msg["severity"] in ALERT_SEVERITIES and opened_recently(msg["opened"], settings.alert_notify_max_age_hours):
                            new_alerts.append(msg)

            result.records_saved = len(messages)

            # Absence-based closure — mark hitachi alerts resolved once they drop
            # out of the array's current open set (own cursor, like netapp's
            # auto-resolve). Guarded by fetch_ok so a failed fetch can't wrongly
            # resolve everything.
            if data.get("fetch_ok"):
                try:
                    with get_db_cursor() as cursor:
                        resolve_absent_alerts(cursor, SCHEMA, self.array_name, "hitachi",
                                              [m["message_id"] for m in messages])
                except Exception as e:
                    logger.warning(f"[{self.array_name}] absence-resolve failed (non-fatal): {e}")

            # Send notifications for new critical/warning alerts
            if new_alerts:
                self._send_notifications(new_alerts)

            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def _send_notifications(self, alerts: List[Dict]):
        for alert in alerts:
            if not settings.teams_webhook_url:
                continue
            try:
                ok = send_teams_alert(
                    array_name=alert["array_name"],
                    vendor="hitachi",
                    severity=alert["severity"],
                    event=alert["event"],
                    component=alert.get("component_name", ""),
                    message_id=str(alert.get("message_id", "")),
                    opened=alert.get("opened", ""),
                    webhook_url=settings.teams_webhook_url,
                )

                if ok:
                    self._mark_notified(alert["array_name"], alert["message_id"])
            except Exception as e:
                logger.warning(f"[{self.array_name}] Teams notification failed: {e}")

    def _mark_notified(self, array_name: str, message_id: int):
        """Mark an alert as notified so it isn't re-sent on the next cycle."""
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    f"UPDATE {SCHEMA}.messages SET teams_notified=GETDATE() "
                    f"WHERE array_name=? AND message_id=?",
                    (array_name, message_id),
                )
        except Exception as e:
            logger.error(f"[{self.array_name}] Failed to mark notification: {e}")

    def disconnect(self):
        self.client.disconnect()


