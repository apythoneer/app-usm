"""
Hitachi VSP Metrics Collector — capacity + system info from Configuration Manager REST API.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.hitachi.client import HitachiVSPClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.hitachi.metrics")
settings = get_settings()
SCHEMA = settings.db_schema

# VSP Configuration Manager REST API returns pool capacity in MB
POOL_CAP_UNIT = 1024 * 1024  # MB → bytes


@CollectorRegistry.register("hitachi", "metrics")
class HitachiMetricsCollector(BaseCollector):
    VENDOR = "hitachi"
    COLLECTOR_TYPE = "metrics"

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
        metrics: Dict[str, Any] = {
            "array_name": self.array_name, "vendor": "hitachi",
            "collected_at": datetime.now().isoformat(),
        }

        # Storage system info
        storages = self.client.get("", timeout=15)
        # The base storages endpoint returns the storage info when accessed directly
        # Actually we need to get it from the parent
        info_resp = self.client.session.get(
            f"{self.client.base_url}/storages/{self.client.storage_device_id}",
            timeout=15,
        )
        if info_resp and info_resp.status_code == 200:
            info = info_resp.json()
            metrics["purity_version"] = info.get("firmwareVersion", info.get("dkcMicroVersion", ""))
            metrics["array_status"] = "healthy"
            metrics["controller_status"] = "healthy"
            metrics["serial"] = str(info.get("serialNumber", ""))
            metrics["model"] = info.get("model", "")

        # Pool capacity — aggregate all pools
        pools_resp = self.client.get("pools", timeout=30)
        if pools_resp and pools_resp.get("data"):
            total_capacity = 0
            total_used = 0
            total_reduction_sum = 0
            pool_count = 0

            for pool in pools_resp["data"]:
                # totalPoolCapacity is in MB
                pool_total_mb = pool.get("totalPoolCapacity", 0)
                pool_total_bytes = pool_total_mb * POOL_CAP_UNIT

                used_pct = pool.get("usedCapacityRate", 0)
                pool_used_bytes = int(pool_total_bytes * used_pct / 100)

                total_capacity += pool_total_bytes
                total_used += pool_used_bytes

                # Data reduction
                reduction = pool.get("dataReductionRate", 0)
                if reduction and reduction > 0:
                    total_reduction_sum += reduction
                    pool_count += 1

            if total_capacity:
                metrics["capacity_total"] = total_capacity
                metrics["capacity_used"] = total_used
                metrics["capacity_used_pct"] = round(total_used / total_capacity * 100, 2)
                if pool_count and total_reduction_sum:
                    metrics["data_reduction"] = round(total_reduction_sum / pool_count, 2)

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
                cursor.execute(f"SELECT id FROM {SCHEMA}.metrics_current WHERE array_name = ?", (array_name,))
                existing = cursor.fetchone()

                if existing:
                    cursor.execute(
                        f"""UPDATE {SCHEMA}.metrics_current SET
                            vendor=?, purity_version=?, capacity_total=?, capacity_used=?,
                            capacity_used_pct=?, data_reduction=?,
                            array_status=?, controller_status=?, collected_at=?
                        WHERE array_name=?""",
                        ("hitachi", data.get("purity_version", ""),
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
                        (array_name, "hitachi", data.get("purity_version", ""),
                         data.get("capacity_total", 0), data.get("capacity_used", 0),
                         data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                         data.get("array_status", "unknown"), data.get("controller_status", "unknown"),
                         data["collected_at"]),
                    )

                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, vendor, collected_at, capacity_total, capacity_used,
                        capacity_used_pct, data_reduction
                    ) VALUES (?, 'hitachi', GETDATE(), ?, ?, ?, ?)""",
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
