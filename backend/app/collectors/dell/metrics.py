"""
Dell EMC Unity Metrics Collector — capacity + system info from Unisphere REST API.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.dell.client import DellUnityClient
from app.collectors.health_rules import unity_health
from app.collectors.hardware_rules import unity_inventory
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.dell.metrics")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("dell", "metrics")
class DellMetricsCollector(BaseCollector):
    VENDOR = "dell"
    COLLECTOR_TYPE = "metrics"

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
        metrics: Dict[str, Any] = {
            "array_name": self.array_name, "vendor": "dell",
            "collected_at": datetime.now().isoformat(),
        }

        # System info — try with fields, fallback to no fields on 422
        sys_resp = self.client.get("system",
                                   fields="name,model,serialNumber,softwareVersion,health")
        if not sys_resp:
            # Some Unity versions don't support all fields — try minimal
            sys_resp = self.client.get("system", fields="name,model,health")
        if not sys_resp:
            # Last resort — no fields filter
            sys_resp = self.client.get("system")
        system_health = None
        if sys_resp and sys_resp.get("entries"):
            content = sys_resp["entries"][0].get("content", {})
            metrics["purity_version"] = content.get("softwareVersion", "")
            metrics["model"] = content.get("model", "")
            metrics["serial"] = content.get("serialNumber", "")
            system_health = content.get("health")

        # Hardware health — Unity's system health is its own rollup of every
        # component (HealthEnum: 0 UNKNOWN, 5 OK, 7 OK_BUT, 10 DEGRADED, 15 MINOR,
        # 20 MAJOR, 25 CRITICAL, 30 NON_RECOVERABLE), plus each storage processor's
        # health for the controller detail. No signal stays "unknown" rather than
        # the old hardcoded "healthy".
        sps = self.client.get("storageProcessor", fields="name,health,model")
        state, detail = unity_health(system_health=system_health, storage_processors=sps)
        metrics["health_state"] = state
        metrics["health_detail"] = detail
        metrics["array_status"] = metrics["controller_status"] = state or "unknown"

        # Hardware inventory for the wireframe — every Unity component type carries
        # its own health; empty slots / unused ports are flagged by descriptionId.
        page = {"per_page": 2000}
        get = lambda res, f: self.client.get(res, fields=f, params=page)
        metrics["hardware"] = unity_inventory(
            sps=sps,
            dpe=get("dpe", "name,health,model"),
            daes=get("dae", "name,health"),
            disks=get("disk", "name,health,slotNumber,parentDae,parentDpe,size,isInUse"),
            eth_ports=get("ethernetPort", "name,health,speed,isLinkUp,storageProcessor"),
            fc_ports=get("fcPort", "name,health,currentSpeed,storageProcessor"),
            sas_ports=get("sasPort", "name,health,currentSpeed,parent"),
            psus=get("powerSupply", "name,health,parent"),
            fans=get("fan", "name,health,parent"),
            batteries=get("battery", "name,health,parent"),
            lccs=get("lcc", "name,health,parent"),
            io_modules=get("ioModule", "name,health,parent"),
            memory=get("memoryModule", "name,health,parent"),
        )

        # Pool capacity — aggregate all pools
        pools_resp = self.client.get("pool",
                                     fields="name,sizeFree,sizeTotal,sizeUsed,sizeSubscribed")
        if pools_resp and pools_resp.get("entries"):
            total_capacity = 0
            total_used = 0
            for entry in pools_resp["entries"]:
                content = entry.get("content", {})
                pool_total = content.get("sizeTotal", 0)
                pool_used = content.get("sizeUsed", 0)
                total_capacity += pool_total
                total_used += pool_used

            if total_capacity:
                metrics["capacity_total"] = total_capacity
                metrics["capacity_used"] = total_used
                metrics["capacity_used_pct"] = round(total_used / total_capacity * 100, 2)

        return metrics

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        # Fail-safe: collect() only sets capacity_total when the capacity fetch
        # succeeded, so a missing value means this cycle's capacity call failed.
        # Writing it would default to 0 — zeroing metrics_current AND appending a
        # 0 cliff to metrics_history that corrupts capacity/projection data. Keep
        # last-known values instead (mirrors the volumes empty-collect guard).
        if data.get("capacity_total") is None:
            self.logger.warning(
                f"[{data.get('array_name')}] capacity unavailable this cycle — "
                f"keeping last-known metrics (skipped write to avoid a zero cliff)"
            )
            return True
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
                        ("dell", data.get("purity_version", ""),
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
                        (array_name, "dell", data.get("purity_version", ""),
                         data.get("capacity_total", 0), data.get("capacity_used", 0),
                         data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                         data.get("array_status", "unknown"), data.get("controller_status", "unknown"),
                         data["collected_at"]),
                    )

                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, vendor, collected_at, capacity_total, capacity_used,
                        capacity_used_pct, data_reduction
                    ) VALUES (?, 'dell', GETDATE(), ?, ?, ?, ?)""",
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
