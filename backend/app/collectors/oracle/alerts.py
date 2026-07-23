"""
Oracle ZFS Alert Collector — collects alerts from ZFS REST API.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.oracle.client import OracleZFSClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.collectors.alert_utils import opened_recently, resolve_absent_alerts
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.oracle.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}

_SEVERITY_MAP = {
    "critical": "critical",
    "major": "warning",
    "minor": "info",
    "warning": "warning",
    "info": "info",
}


@CollectorRegistry.register("oracle", "alerts")
class OracleAlertsCollector(BaseCollector):
    VENDOR = "oracle"
    COLLECTOR_TYPE = "alerts"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"Oracle array '{array_config.name}' has no cred_key")
        self.client = OracleZFSClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        messages = []

        data = self.client.get("problem/v1/problems")
        if data and data.get("problems"):
            for prob_entry in data["problems"]:
                # Handle both flat and nested response formats
                prob = prob_entry.get("problem", prob_entry) if isinstance(prob_entry.get("problem"), dict) else prob_entry
                severity_raw = (prob.get("severity", "") or "").lower()
                severity = _SEVERITY_MAP.get(severity_raw, "info")

                messages.append({
                    "array_name": self.array_name,
                    "vendor": "oracle",
                    "message_id": hash(prob.get("uuid", "")) % 2147483647,
                    "event": prob.get("description", "")[:500],
                    "severity": severity,
                    "component_type": prob.get("type", ""),
                    "component_name": (prob.get("impact", "") or "")[:255],
                    "opened": prob.get("diagnosed", ""),
                    "closed": "",
                    "expected": "",
                    "actual": prob.get("action", ""),
                    "collected_at": datetime.now().isoformat(),
                })

        # Note: /system/v1/alerts does not exist on ZFS — only /problem/v1/problems

        logger.info(f"[{self.array_name}] {len(messages)} alerts collected")
        return {"messages": messages, "fetch_ok": data is not None}

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
                            (msg["array_name"], "oracle", msg["message_id"], msg["event"],
                             msg["severity"], msg["component_type"], msg["component_name"],
                             msg["opened"], msg["closed"], msg["expected"], msg["actual"],
                             msg["collected_at"]),
                        )
                        if msg["severity"] in ALERT_SEVERITIES and opened_recently(msg["opened"], settings.alert_notify_max_age_hours):
                            new_alerts.append(msg)

            result.records_saved = len(messages)

            # Absence-based closure — mark oracle alerts resolved once they drop
            # out of the array's current open set (own cursor, like netapp's
            # auto-resolve). Guarded by fetch_ok so a failed fetch can't wrongly
            # resolve everything.
            if data.get("fetch_ok"):
                try:
                    with get_db_cursor() as cursor:
                        resolve_absent_alerts(cursor, SCHEMA, self.array_name, "oracle",
                                              [m["message_id"] for m in messages])
                except Exception as e:
                    logger.warning(f"[{self.array_name}] absence-resolve failed (non-fatal): {e}")
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
                    array_name=alert["array_name"], vendor="oracle",
                    severity=alert["severity"], event=alert["event"],
                    component=f"{alert['component_type']}: {alert['component_name']}",
                    message_id=str(alert.get("message_id", "")),
                    opened=alert.get("opened", ""),
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


