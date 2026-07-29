"""
NetApp StorageGrid Alert Collector — grid alarms/alerts from the Management API.

Registered as the 'storagegrid' vendor (like storagegrid_metrics) so the scheduler
routes StorageGrid admin nodes here instead of the ONTAP 'netapp' alert collector —
which is why those 6 nodes were collecting ZERO alerts (the netapp_alerts job runs
22/28: it never saw them).

Two alert systems coexist across StorageGrid versions:
  * grid/alarms  — the LEGACY alarm system. Validated live on naat1an01: returns 6
    active alarms with sourceId/severity/attributeCode/triggerTime/acknowledgeTime.
  * grid/alerts  — the newer alert system. Returned 0 on this version but is queried
    defensively so newer grids are covered too.
Both are merged. Rows are stored with vendor='netapp' to match storagegrid_metrics
(StorageGrid is presented as NetApp across the platform).

Closure is absence-based (an alarm that clears simply drops off the current set),
identical to the Pure/HPE/Hitachi collectors.
"""

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.netapp.storagegrid_client import StorageGridClient
from app.db.session import get_db_cursor
from app.services.notification import send_teams_alert
from app.collectors.alert_utils import (
    opened_recently, resolve_absent_alerts, dispatch_datadog_new, push_datadog_resolutions,
)
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.storagegrid.alerts")
settings = get_settings()
SCHEMA = settings.db_schema

ALERT_SEVERITIES = {"critical", "warning"}

# StorageGrid alarm/alert severity -> USM severity. 'major' is a genuine problem
# (e.g. a downed network link) so it surfaces as a warning; minor/notice are info.
_SEVERITY_MAP = {
    "critical": "critical",
    "major": "warning",
    "minor": "info",
    "notice": "info",
    "info": "info",
}

# Human labels for the most common legacy alarm attribute codes; unmapped codes
# fall through to the raw code so nothing is dropped.
_ATTR_LABELS = {
    "NLNK": "Network link down/degraded",
    "NRER": "Network receive errors",
    "NTER": "Network transmit errors",
    "NTBR": "Network receive bytes",
    "SSTS": "Storage status",
    "SLSA": "Storage load / space",
    "SAVP": "Available object storage low",
    "SHLH": "Services health",
    "MINS": "Installed services count",
    "VMFI": "Available metadata space low",
    "MISS": "Node/service unreachable",
    "OQRT": "Objects quarantined",
    "RIRA": "Repairs attempted",
}


@CollectorRegistry.register("storagegrid", "alerts")
class StorageGridAlertsCollector(BaseCollector):
    VENDOR = "storagegrid"
    COLLECTOR_TYPE = "alerts"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"StorageGrid node '{array_config.name}' has no cred_key")
        self.client = StorageGridClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    @staticmethod
    def _mid(key: str) -> int:
        """Stable INT message_id (within 2^31-1) from an alarm/alert identity."""
        return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % 2147483647

    def collect(self) -> Dict[str, Any]:
        messages: List[Dict] = []
        any_ok = False

        # ---- Legacy alarms (validated live) ----------------------------------
        alarms = self.client.get("grid/alarms")
        if alarms is not None:
            any_ok = True
            for a in (alarms.get("data") or []):
                code = a.get("attributeCode") or "ALARM"
                sev = _SEVERITY_MAP.get((a.get("severity") or "").lower(), "info")
                # sourceId identifies the NODE/source, not the alarm — one node emits
                # many attribute alarms that share a sourceId. Key the message_id on
                # source + attributeCode + attributeIndex so distinct alarms don't
                # collapse onto one row (which silently dropped ~half of them).
                src = str(a.get("sourceId") or "")
                idx = str(a.get("attributeIndex", ""))
                label = _ATTR_LABELS.get(code)
                val = a.get("triggerValue", "")
                detail = f"{label} [{code}]" if label else f"{code} alarm"
                messages.append({
                    "array_name": self.array_name,
                    "vendor": "netapp",
                    "message_id": self._mid(f"alarm:{src}:{code}:{idx}"),
                    "event": f"{detail} (value={val})"[:500],
                    "severity": sev,
                    "component_type": "alarm",
                    "component_name": code[:255],
                    "opened": a.get("triggerTime", "") or "",
                    "closed": "",
                    "expected": "",
                    "actual": str(a.get("triggerValue", "")),
                    "collected_at": datetime.now().isoformat(),
                })

        # ---- New alert system (defensive; 0 rows on the current version) ------
        alerts = self.client.get("grid/alerts")
        if alerts is not None:
            any_ok = True
            for a in (alerts.get("data") or []):
                sev = _SEVERITY_MAP.get((a.get("severity") or "").lower(), "info")
                ident = str(a.get("id") or a.get("name") or a.get("ruleId") or "")
                node = a.get("nodeName") or a.get("node") or ""
                event = a.get("summary") or a.get("message") or a.get("name") or "StorageGrid alert"
                messages.append({
                    "array_name": self.array_name,
                    "vendor": "netapp",
                    "message_id": self._mid(f"alert:{ident}:{node}"),
                    "event": str(event)[:500],
                    "severity": sev,
                    "component_type": "alert",
                    "component_name": str(node)[:255],
                    "opened": a.get("startTime") or a.get("triggerTime") or "",
                    "closed": "",
                    "expected": "",
                    "actual": "",
                    "collected_at": datetime.now().isoformat(),
                })

        logger.info(f"[{self.array_name}] {len(messages)} StorageGrid alerts/alarms collected")
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
                                opened=?, actual=?, collected_at=?, resolved=0
                            WHERE array_name=? AND message_id=?""",
                            (msg["event"], msg["severity"], msg["component_type"],
                             msg["component_name"], msg["opened"], msg["actual"],
                             msg["collected_at"], msg["array_name"], msg["message_id"]),
                        )
                        if (not existing[1] and not existing[2]
                                and msg["severity"] in ALERT_SEVERITIES
                                and opened_recently(msg["opened"], settings.alert_notify_max_age_hours)):
                            new_alerts.append(msg)
                    else:
                        cursor.execute(
                            f"""INSERT INTO {SCHEMA}.messages (
                                array_name, vendor, message_id, event, severity,
                                component_type, component_name, opened, closed,
                                expected, actual, collected_at, suppressed, resolved
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                            (msg["array_name"], "netapp", msg["message_id"], msg["event"],
                             msg["severity"], msg["component_type"], msg["component_name"],
                             msg["opened"], msg["closed"], msg["expected"], msg["actual"],
                             msg["collected_at"]),
                        )
                        if (msg["severity"] in ALERT_SEVERITIES
                                and opened_recently(msg["opened"], settings.alert_notify_max_age_hours)):
                            new_alerts.append(msg)

            result.records_saved = len(messages)

            # Absence-based closure — an alarm that clears drops off the current
            # set. Stored as vendor='netapp' (like storagegrid_metrics), so resolve
            # by (array_name, 'netapp'); this SG node is never touched by the ONTAP
            # netapp collector, so the scope is exactly this node's alarms.
            if data.get("fetch_ok"):
                try:
                    with get_db_cursor() as cursor:
                        resolve_absent_alerts(cursor, SCHEMA, self.array_name, "netapp",
                                              [m["message_id"] for m in messages])
                except Exception as e:
                    logger.warning(f"[{self.array_name}] absence-resolve failed (non-fatal): {e}")
                push_datadog_resolutions(SCHEMA, self.array_name, "netapp")

            if new_alerts:
                self._send_notifications(new_alerts)
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def _send_notifications(self, alerts: List[Dict]):
        # Push to Datadog first (independent of Teams config)
        dispatch_datadog_new(SCHEMA, "netapp", alerts)
        for alert in alerts:
            if not settings.teams_webhook_url:
                continue
            try:
                ok = send_teams_alert(
                    array_name=alert["array_name"], vendor="netapp",
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
