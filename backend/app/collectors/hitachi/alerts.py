"""
Hitachi VSP Alert Collector — collects alerts from Configuration Manager REST API.
Auto-registered with CollectorRegistry.

Note: The VSP REST API alerts endpoint may return HTTP 400 on some firmware versions.
In that case, we log a warning and return empty alerts (no critical failure).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.hitachi.client import HitachiVSPClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.hitachi.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}

_SEVERITY_MAP = {
    "Acute": "critical",
    "Serious": "warning",
    "Moderate": "info",
    "Service": "info",
}


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

        # Try alerts endpoint — may not be available on all firmware versions
        alerts_resp = self.client.get("alerts", timeout=30)
        if alerts_resp and alerts_resp.get("data"):
            for alert in alerts_resp["data"]:
                severity_raw = alert.get("severity", "")
                severity = _SEVERITY_MAP.get(severity_raw, "info")

                alert_id = alert.get("alertId") or alert.get("alertIndex", 0)
                messages.append({
                    "array_name": self.array_name,
                    "vendor": "hitachi",
                    "message_id": alert_id if isinstance(alert_id, int) else hash(str(alert_id)) % 2147483647,
                    "event": (alert.get("description", "") or alert.get("errorDetail", ""))[:500],
                    "severity": severity,
                    "component_type": alert.get("errorSection", ""),
                    "component_name": alert.get("location", "")[:255] if alert.get("location") else "",
                    "opened": alert.get("occurredTime", "") or alert.get("referenceCode", ""),
                    "closed": "",
                    "expected": "",
                    "actual": alert.get("actionCode", "") or alert.get("errorCode", ""),
                    "collected_at": datetime.now().isoformat(),
                })
        else:
            logger.debug(f"[{self.array_name}] No alerts endpoint or empty response")

        logger.info(f"[{self.array_name}] {len(messages)} alerts collected")
        return {"messages": messages}

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
                                opened=?, collected_at=?
                            WHERE array_name=? AND message_id=?""",
                            (msg["event"], msg["severity"], msg["component_type"],
                             msg["component_name"], msg["opened"], msg["collected_at"],
                             msg["array_name"], msg["message_id"]),
                        )
                        if not existing[1] and not existing[2] and msg["severity"] in ALERT_SEVERITIES:
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
                        if msg["severity"] in ALERT_SEVERITIES:
                            new_alerts.append(msg)

            result.records_saved = len(messages)

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
                send_teams_alert(
                    array_name=alert["array_name"],
                    vendor="hitachi",
                    severity=alert["severity"],
                    event=alert["event"],
                    component=alert.get("component_name", ""),
                    webhook_url=settings.teams_webhook_url,
                )
            except Exception as e:
                logger.warning(f"[{self.array_name}] Teams notification failed: {e}")

    def disconnect(self):
        self.client.disconnect()
