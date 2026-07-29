"""
Dell EMC Unity Alert Collector — collects alerts from Unisphere REST API.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.dell.client import DellUnityClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.collectors.alert_utils import (
    opened_recently, resolve_absent_alerts, dispatch_datadog_new, push_datadog_resolutions,
)
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.dell.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}

# Unity severity values: 0=Emergency, 2=Alert, 4=Critical, 6=Error, 8=Warning, 10=Notice, 12=Info
_SEVERITY_MAP = {
    0: "critical",
    2: "critical",
    4: "critical",
    6: "warning",
    8: "warning",
    10: "info",
    12: "info",
}


@CollectorRegistry.register("dell", "alerts")
class DellAlertsCollector(BaseCollector):
    VENDOR = "dell"
    COLLECTOR_TYPE = "alerts"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"Dell array '{array_config.name}' has no cred_key")
        self.client = DellUnityClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        messages = []

        alerts_resp = self.client.get(
            "alert",
            fields="severity,message,timestamp,component,isAcknowledged",
            params={"per_page": 100},
        )
        if alerts_resp and alerts_resp.get("entries"):
            for entry in alerts_resp["entries"]:
                content = entry.get("content", {})
                severity_num = content.get("severity", 12)
                severity = _SEVERITY_MAP.get(severity_num, "info")

                alert_id = content.get("id", "")
                msg_id = hash(str(alert_id)) % 2147483647 if alert_id else 0

                component = content.get("component", {})
                comp_name = ""
                if isinstance(component, dict):
                    comp_name = component.get("id", "")

                messages.append({
                    "array_name": self.array_name,
                    "vendor": "dell",
                    "message_id": msg_id,
                    "event": (content.get("message", "") or "")[:500],
                    "severity": severity,
                    "component_type": "unity",
                    "component_name": comp_name[:255],
                    "opened": content.get("timestamp", ""),
                    "closed": "",
                    "expected": "",
                    "actual": "",
                    "collected_at": datetime.now().isoformat(),
                })

        logger.info(f"[{self.array_name}] {len(messages)} alerts collected")
        return {"messages": messages, "fetch_ok": alerts_resp is not None}

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
                                opened=?, collected_at=?, resolved=0,
                                occurrence_count = occurrence_count + CASE WHEN resolved=1 THEN 1 ELSE 0 END,
                                last_seen = GETDATE()
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
                            (msg["array_name"], "dell", msg["message_id"], msg["event"],
                             msg["severity"], msg["component_type"], msg["component_name"],
                             msg["opened"], msg["closed"], msg["expected"], msg["actual"],
                             msg["collected_at"]),
                        )
                        if msg["severity"] in ALERT_SEVERITIES and opened_recently(msg["opened"], settings.alert_notify_max_age_hours):
                            new_alerts.append(msg)

            result.records_saved = len(messages)

            # Absence-based closure — mark dell alerts resolved once they drop
            # out of the array's current open set (own cursor, like netapp's
            # auto-resolve). Guarded by fetch_ok so a failed fetch can't wrongly
            # resolve everything.
            if data.get("fetch_ok"):
                try:
                    with get_db_cursor() as cursor:
                        resolve_absent_alerts(cursor, SCHEMA, self.array_name, "dell",
                                              [m["message_id"] for m in messages])
                except Exception as e:
                    logger.warning(f"[{self.array_name}] absence-resolve failed (non-fatal): {e}")
            # Resolve in Datadog any alerts that just closed on the array
            push_datadog_resolutions(SCHEMA, self.array_name, "dell")
            if new_alerts:
                self._send_notifications(new_alerts)
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def _send_notifications(self, alerts: List[Dict]):
        # Push to Datadog first (independent of Teams config)
        dispatch_datadog_new(SCHEMA, "dell", alerts)
        for alert in alerts:
            if not settings.teams_webhook_url:
                continue
            try:
                ok = send_teams_alert(
                    array_name=alert["array_name"], vendor="dell",
                    severity=alert["severity"], event=alert["event"],
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


