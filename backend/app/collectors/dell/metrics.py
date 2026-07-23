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
        if sys_resp and sys_resp.get("entries"):
            content = sys_resp["entries"][0].get("content", {})
            metrics["purity_version"] = content.get("softwareVersion", "")
            metrics["model"] = content.get("model", "")
            metrics["serial"] = content.get("serialNumber", "")

            health = content.get("health", {})
            health_value = health.get("value", 0) if isinstance(health, dict) else 0
            # Unity health: 5=OK, 7=Degraded, 10=Minor, 15=Major, 20=Critical
            if health_value <= 5:
                metrics["array_status"] = "healthy"
            elif health_value <= 10:
                metrics["array_status"] = "degraded"
            else:
                metrics["array_status"] = "critical"
            metrics["controller_status"] = metrics["array_status"]

        # Default status if system info unavailable
        if "array_status" not in metrics:
            metrics["array_status"] = "healthy"
            metrics["controller_status"] = "healthy"

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
