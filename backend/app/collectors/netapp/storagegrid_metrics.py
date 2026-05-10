"""
NetApp StorageGrid Metrics Collector — grid health + capacity from Management API.
Registered as 'storagegrid' vendor to avoid conflict with ONTAP 'netapp' collectors.
Only admin nodes should be enabled — they provide grid-wide data.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.netapp.storagegrid_client import StorageGridClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.storagegrid.metrics")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("storagegrid", "metrics")
class StorageGridMetricsCollector(BaseCollector):
    VENDOR = "storagegrid"
    COLLECTOR_TYPE = "metrics"

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

    def collect(self) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {
            "array_name": self.array_name, "vendor": "netapp",
            "collected_at": datetime.now().isoformat(),
        }

        # Grid health
        health = self.client.get("grid/health")
        if health and health.get("data"):
            h = health["data"]
            alarms = h.get("alarms", {})
            alerts = h.get("alerts", {})
            nodes = h.get("nodes", {})

            crit = alarms.get("critical", 0) + alerts.get("critical", 0)
            major = alarms.get("major", 0) + alerts.get("major", 0)

            if crit > 0:
                metrics["array_status"] = "critical"
            elif major > 0:
                metrics["array_status"] = "degraded"
            else:
                metrics["array_status"] = "healthy"
            metrics["controller_status"] = metrics["array_status"]

            connected = nodes.get("connected", 0)
            total_nodes = connected + nodes.get("administratively-down", 0) + nodes.get("unknown", 0)
            metrics["node_count"] = total_nodes
            metrics["nodes_connected"] = connected

        # Grid config for version
        config = self.client.get("grid/config")
        if config and config.get("data"):
            metrics["purity_version"] = config["data"].get("productVersion", "")

        # Capacity from grid topology (traverse to find storage nodes)
        topology = self.client.get("grid/health/topology")
        if topology and topology.get("data"):
            total_cap, used_cap = self._extract_capacity_from_topology(topology["data"])
            if total_cap > 0:
                metrics["capacity_total"] = total_cap
                metrics["capacity_used"] = used_cap
                metrics["capacity_used_pct"] = round(used_cap / total_cap * 100, 2)

        return metrics

    def _extract_capacity_from_topology(self, topo: dict) -> tuple:
        """Walk the topology tree to find storage capacity in attributes."""
        total = 0
        used = 0

        # The topology is a nested tree: Grid → Site → Node → Component → Attribute
        # Look for storageBytesInstalled and storageBytesUsed at the grid level
        attrs = topo.get("attributes", {})
        if "installedStorageCapacity" in attrs:
            total = attrs.get("installedStorageCapacity", {}).get("value", 0)
        if "dataObjectsBytesUsed" in attrs:
            used = attrs.get("dataObjectsBytesUsed", {}).get("value", 0)

        # If not at top level, walk children
        if total == 0:
            for child in topo.get("children", []):
                ct, cu = self._extract_capacity_from_topology(child)
                total += ct
                used += cu

        return total, used

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        array_name = data["array_name"]
        try:
            with get_db_cursor() as cursor:
                cursor.execute(f"SELECT id FROM {SCHEMA}.metrics_current WHERE array_name = ?",
                               (array_name,))
                existing = cursor.fetchone()

                if existing:
                    cursor.execute(
                        f"""UPDATE {SCHEMA}.metrics_current SET
                            vendor=?, purity_version=?, capacity_total=?, capacity_used=?,
                            capacity_used_pct=?, data_reduction=?,
                            array_status=?, controller_status=?, collected_at=?
                        WHERE array_name=?""",
                        ("netapp", data.get("purity_version", ""),
                         data.get("capacity_total", 0), data.get("capacity_used", 0),
                         data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                         data.get("array_status", "unknown"), data.get("controller_status", "unknown"),
                         data["collected_at"], array_name),
                    )
                else:
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.metrics_current (
                            array_name, vendor, purity_version, capacity_total, capacity_used,
                            capacity_used_pct, data_reduction, array_status, controller_status, collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (array_name, "netapp", data.get("purity_version", ""),
                         data.get("capacity_total", 0), data.get("capacity_used", 0),
                         data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                         data.get("array_status", "unknown"), data.get("controller_status", "unknown"),
                         data["collected_at"]),
                    )

                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, vendor, collected_at, capacity_total, capacity_used,
                        capacity_used_pct, data_reduction
                    ) VALUES (?, 'netapp', GETDATE(), ?, ?, ?, ?)""",
                    (array_name, data.get("capacity_total", 0), data.get("capacity_used", 0),
                     data.get("capacity_used_pct", 0), data.get("data_reduction", 1)),
                )

            result.records_saved = 2
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
