"""
Pure Storage Alert Collector v2
Collects open alerts, persists to DB, fires Teams/ServiceNow notifications for new ones.
Auto-registered with CollectorRegistry.
"""

import logging
import subprocess
import os
from datetime import datetime
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.pure.client import PureClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.collectors.alert_utils import opened_recently, resolve_absent_alerts
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.pure.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}
SNOW_SEVERITIES = set(os.environ.get("SNOW_SEVERITIES", "critical").split(","))
ZABBIX_BIN = os.environ.get("ZABBIX_BIN", "/usr/local/zabbix/bin")
ZABBIX_CONF = os.environ.get("ZABBIX_CONF", "/usr/local/zabbix/conf/zabbix_agentd.conf")


@CollectorRegistry.register("pure", "alerts")
class PureAlertsCollector(BaseCollector):
    VENDOR = "pure"
    COLLECTOR_TYPE = "alerts"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        self.client = PureClient(array_config.name)

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        messages = []
        # Fetch UNFILTERED, not ?open=true. On the FA REST v1.19 API the
        # `open=true` filter returns 0 rows even when the array has open alerts,
        # so USM was collecting NO Pure alerts at all. The bare `message` endpoint
        # returns the current open-alert set (each has closed=None); a closed alert
        # simply drops off this list (it never reappears with a closed timestamp),
        # which is why closure is handled by absence-based resolve in save().
        data = self.client.get("message")
        fetch_ok = data is not None
        if data:
            for msg in data:
                sev = (msg.get("current_severity") or msg.get("severity") or "").lower()
                messages.append(
                    {
                        "array_name": self.array_name,
                        "vendor": "pure",
                        "message_id": msg.get("id"),
                        "event": msg.get("event", ""),
                        "severity": sev,
                        "component_type": msg.get("component_type", ""),
                        "component_name": msg.get("component_name", ""),
                        "opened": msg.get("opened", ""),
                        "closed": msg.get("closed") or "",
                        "expected": msg.get("expected", ""),
                        "actual": msg.get("actual", ""),
                        "collected_at": datetime.now().isoformat(),
                    }
                )
        logger.info(f"[{self.array_name}] {len(messages)} open alerts collected")
        return {"messages": messages, "fetch_ok": fetch_ok}

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        messages = data.get("messages", [])
        new_alerts: List[Dict] = []

        try:
            with get_db_cursor() as cursor:
                for msg in messages:
                    cursor.execute(
                        f"SELECT id, alerted, teams_notified, snow_ticket "
                        f"FROM {SCHEMA}.messages WHERE array_name=? AND message_id=?",
                        (msg["array_name"], msg["message_id"]),
                    )
                    existing = cursor.fetchone()

                    if existing:
                        cursor.execute(
                            f"""UPDATE {SCHEMA}.messages SET
                                event=?, severity=?, component_type=?, component_name=?,
                                opened=?, closed=?, expected=?, actual=?, collected_at=?,
                                resolved=CASE WHEN ? IS NOT NULL AND ?!='' THEN 1 ELSE 0 END
                            WHERE array_name=? AND message_id=?""",
                            (
                                msg["event"], msg["severity"], msg["component_type"],
                                msg["component_name"], msg["opened"], msg["closed"],
                                msg["expected"], msg["actual"], msg["collected_at"],
                                msg["closed"], msg["closed"],
                                msg["array_name"], msg["message_id"],
                            ),
                        )
                        # Notify if not yet notified AND recently opened (the
                        # recency gate stops a backfill of long-open alerts from
                        # re-paging everyone; genuinely new alerts still pass).
                        alerted, teams_notified, snow_ticket = existing[1], existing[2], existing[3]
                        if (not alerted and not teams_notified
                                and msg["severity"] in ALERT_SEVERITIES
                                and opened_recently(msg["opened"], settings.alert_notify_max_age_hours)):
                            msg["_needs_teams"] = True
                            msg["_needs_snow"] = not snow_ticket and msg["severity"] in SNOW_SEVERITIES
                            new_alerts.append(msg)
                    else:
                        cursor.execute(
                            f"""INSERT INTO {SCHEMA}.messages (
                                array_name, message_id, event, severity,
                                component_type, component_name, opened, closed,
                                expected, actual, collected_at, suppressed, resolved
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                            (
                                msg["array_name"], msg["message_id"], msg["event"],
                                msg["severity"], msg["component_type"], msg["component_name"],
                                msg["opened"], msg["closed"], msg["expected"], msg["actual"],
                                msg["collected_at"],
                            ),
                        )
                        if (msg["severity"] in ALERT_SEVERITIES
                                and opened_recently(msg["opened"], settings.alert_notify_max_age_hours)):
                            msg["_needs_teams"] = True
                            msg["_needs_snow"] = msg["severity"] in SNOW_SEVERITIES
                            new_alerts.append(msg)

                # Absence-based resolution: any Pure alert we had open that is no
                # longer in the array's current open set has closed on the array —
                # mark it resolved. Guarded by fetch_ok so a failed/empty API call
                # can't wrongly resolve everything.
                if data.get("fetch_ok"):
                    current_ids = [m["message_id"] for m in messages]
                    resolve_absent_alerts(cursor, SCHEMA, self.array_name, "pure", current_ids)

            result.records_saved = len(messages)

            # Fire notifications outside the DB transaction
            self._send_notifications(new_alerts)
            return True

        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def _send_notifications(self, alerts: List[Dict]):
        for alert in alerts:
            if alert.get("_needs_teams"):
                ok = send_teams_alert(
                    array_name=alert["array_name"],
                    vendor="pure",
                    severity=alert["severity"],
                    event=alert["event"],
                    component=f"{alert['component_type']}: {alert['component_name']}",
                    message_id=str(alert.get("message_id", "")),
                    opened=alert.get("opened", ""),
                )

                if ok:
                    self._mark_notified(alert["array_name"], alert["message_id"], "teams")

            if alert.get("_needs_snow"):
                self._create_snow_ticket(alert)

    def _mark_notified(self, array_name: str, message_id: int, ntype: str):
        ts = datetime.now().isoformat()
        with get_db_cursor() as cursor:
            if ntype == "teams":
                cursor.execute(
                    f"UPDATE {SCHEMA}.messages SET alerted=?, teams_notified=GETDATE() "
                    f"WHERE array_name=? AND message_id=?",
                    (ts, array_name, message_id),
                )
            elif ntype == "snow":
                cursor.execute(
                    f"UPDATE {SCHEMA}.messages SET snow_ticket=? "
                    f"WHERE array_name=? AND message_id=?",
                    (ts, array_name, message_id),
                )

    def _create_snow_ticket(self, alert: Dict):
        script = os.path.join(ZABBIX_BIN, "zabbix_servicenow_ticket.sh")
        if not os.path.exists(script):
            return
        snow_category = os.environ.get("SNOW_CATEGORY", "Alert > Infrastructure")
        snow_ci = os.environ.get("SNOW_CI", "Pure Storage CBS")
        snow_location = os.environ.get("SNOW_LOCATION", "Denver")
        snow_group = os.environ.get("SNOW_GROUP", "")
        sev_map = {"critical": 1, "warning": 2, "info": 3}
        sev = sev_map.get(alert["severity"], 3)
        desc = (
            f"Pure Storage Alert\n"
            f"Array: {alert['array_name']}\nSeverity: {alert['severity'].upper()}\n"
            f"Component: {alert['component_type']}: {alert['component_name']}\n"
            f"Event: {alert['event']}\nOpened: {alert['opened']}\n"
            f"Expected: {alert.get('expected', 'N/A')}\nActual: {alert.get('actual', 'N/A')}"
        )
        cmd = [script]
        if snow_group:
            cmd.extend(["-group", snow_group])
        cmd.extend([snow_category, snow_ci, snow_location, str(sev), desc])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                self._mark_notified(alert["array_name"], alert["message_id"], "snow")
        except Exception as e:
            logger.error(f"ServiceNow ticket failed: {e}")

    def disconnect(self):
        self.client.disconnect()
