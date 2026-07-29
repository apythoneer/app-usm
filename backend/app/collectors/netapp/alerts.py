"""
NetApp ONTAP Alert Collector
Collects EMS (Event Management System) events from ONTAP REST API.
Persists to messages table, fires Teams notifications for new critical/warning events.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.netapp.client import NetAppClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.collectors.alert_utils import dispatch_datadog_new, push_datadog_resolutions
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.netapp.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}

# Map ONTAP EMS severity → USM severity
_SEVERITY_MAP = {
    "alert": "critical",
    "emergency": "critical",
    "error": "warning",
    "warning": "info",
    "notice": "info",
}
# Skip these entirely (too noisy)
_SKIP_SEVERITIES = {"informational", "debug", "notice"}


@CollectorRegistry.register("netapp", "alerts")
class NetAppAlertsCollector(BaseCollector):
    VENDOR = "netapp"
    COLLECTOR_TYPE = "alerts"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"NetApp array '{array_config.name}' has no cred_key configured")
        self.client = NetAppClient(
            array_config.name, cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )


    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        messages = []

        # BUG-02 fix: only fetch events from the last 24 hours to avoid
        # paginating through the entire EMS history (was taking ~60s)
        since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

        data = self.client.get(
            "support/ems/events",
            params={
                "fields": "message.name,message.severity,time,log_message,node.name,index",
                "max_records": "200",
                "order_by": "index desc",
                "time": f">{since}",
            },
        )
        events = data.get("records", []) if data else []

        for evt in events:
            raw_severity = (evt.get("message", {}).get("severity") or "").lower()

            # Skip noisy low-level events
            if raw_severity in _SKIP_SEVERITIES:
                continue

            severity = _SEVERITY_MAP.get(raw_severity, "info")

            messages.append({
                "array_name": self.array_name,
                "vendor": "netapp",
                "message_id": evt.get("index", 0),
                "event": evt.get("message", {}).get("name", ""),
                "severity": severity,
                "component_type": evt.get("node", {}).get("name", ""),
                "component_name": (evt.get("log_message") or "")[:255],
                "opened": evt.get("time", ""),
                "closed": "",
                "expected": "",
                "actual": "",
                "collected_at": datetime.now().isoformat(),
            })

        logger.info(f"[{self.array_name}] {len(messages)} EMS events collected (filtered from {len(events)})")
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
                                opened=?, collected_at=?,
                                occurrence_count = occurrence_count + CASE WHEN resolved=1 THEN 1 ELSE 0 END,
                                last_seen = GETDATE()
                            WHERE array_name=? AND message_id=?""",
                            (
                                msg["event"], msg["severity"], msg["component_type"],
                                msg["component_name"], msg["opened"], msg["collected_at"],
                                msg["array_name"], msg["message_id"],
                            ),
                        )
                        alerted, teams_notified = existing[1], existing[2]
                        if not alerted and not teams_notified and msg["severity"] in ALERT_SEVERITIES:
                            msg["_needs_teams"] = True
                            new_alerts.append(msg)
                    else:
                        cursor.execute(
                            f"""INSERT INTO {SCHEMA}.messages (
                                array_name, vendor, message_id, event, severity,
                                component_type, component_name, opened, closed,
                                expected, actual, collected_at, suppressed, resolved
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                            (
                                msg["array_name"], "netapp", msg["message_id"], msg["event"],
                                msg["severity"], msg["component_type"], msg["component_name"],
                                msg["opened"], msg["closed"], msg["expected"], msg["actual"],
                                msg["collected_at"],
                            ),
                        )
                        if msg["severity"] in ALERT_SEVERITIES:
                            msg["_needs_teams"] = True
                            new_alerts.append(msg)

            result.records_saved = len(messages)

            # Fire notifications outside the DB transaction
            self._send_notifications(new_alerts)

            # BUG-04 fix: auto-resolve old NetApp alerts in a separate connection.
            # EMS events are point-in-time (no "closed" concept like Pure alerts).
            # Uses its own cursor since the main save cursor is committed above.
            self._auto_resolve_old_alerts()

            # Resolve in Datadog any alerts that just aged out
            push_datadog_resolutions(SCHEMA, self.array_name, "netapp")

            return True

        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def _auto_resolve_old_alerts(self):
        """Auto-resolve NetApp alerts older than alert_resolve_days (separate connection)."""
        try:
            resolve_days = settings.alert_resolve_days
            with get_db_cursor() as cur:
                cur.execute(
                    f"""UPDATE TOP (5000) {SCHEMA}.messages
                        SET resolved = 1
                        WHERE vendor = 'netapp'
                          AND array_name = ?
                          AND resolved = 0
                          AND TRY_CAST(opened AS DATETIME2) < DATEADD(day, ?, GETDATE())""",
                    (self.array_name, -resolve_days),
                )
                auto_resolved = cur.rowcount
            if auto_resolved:
                logger.info(f"[{self.array_name}] Auto-resolved {auto_resolved} alerts older than {resolve_days} days")
        except Exception as e:
            logger.warning(f"[{self.array_name}] Auto-resolve failed (non-fatal): {e}")

    def _send_notifications(self, alerts: List[Dict]):
        # Push to Datadog first (independent of Teams config)
        dispatch_datadog_new(SCHEMA, "netapp", alerts)
        for alert in alerts:
            if alert.get("_needs_teams"):
                ok = send_teams_alert(
                    array_name=alert["array_name"],
                    vendor="netapp",
                    severity=alert["severity"],
                    event=alert["event"],
                    component=f"{alert['component_type']}: {alert['component_name']}",
                )
                if ok:
                    self._mark_notified(alert["array_name"], alert["message_id"])

    def _mark_notified(self, array_name: str, message_id: int):
        ts = datetime.now().isoformat()
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    f"UPDATE {SCHEMA}.messages SET alerted=?, teams_notified=GETDATE() "
                    f"WHERE array_name=? AND message_id=?",
                    (ts, array_name, message_id),
                )
        except Exception as e:
            logger.error(f"Failed to mark notification: {e}")

    def disconnect(self):
        self.client.disconnect()
