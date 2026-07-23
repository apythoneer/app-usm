"""
Oracle ZFS Storage Appliance Metrics Collector
Collects capacity + performance from ZFS REST API.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.oracle.client import OracleZFSClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.oracle.metrics")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("oracle", "metrics")
class OracleMetricsCollector(BaseCollector):
    VENDOR = "oracle"
    COLLECTOR_TYPE = "metrics"

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
        metrics: Dict[str, Any] = {
            "array_name": self.array_name, "vendor": "oracle",
            "collected_at": datetime.now().isoformat(),
        }

        # System version
        ver = self.client.get("system/v1/version")
        if ver and ver.get("version"):
            v = ver["version"]
            metrics["purity_version"] = v.get("version", "")
            metrics["array_status"] = "healthy"
            metrics["controller_status"] = "healthy"
            # Boot time for uptime
            boot = v.get("boot_time", "")
            if boot:
                metrics["last_reboot"] = boot

        # Storage pools — list first, then get each pool's detail for usage
        pools_list = self.client.get("storage/v1/pools")
        if pools_list and pools_list.get("pools"):
            total = 0
            used = 0
            compression = 0
            pool_count = 0
            for pool_summary in pools_list["pools"]:
                pool_name = pool_summary.get("name", "")
                # Skip exported/offline pools (owned by peer controller)
                if pool_summary.get("status") != "online":
                    continue
                # Get pool detail with usage data
                pool_detail = self.client.get(f"storage/v1/pools/{pool_name}")
                if pool_detail and pool_detail.get("pool"):
                    p = pool_detail["pool"]
                    usage = p.get("usage", {})
                    total += usage.get("total", 0)
                    used += usage.get("used", 0)
                    comp = usage.get("compression", 0)
                    if comp:
                        compression += comp
                        pool_count += 1
            if total:
                metrics["capacity_total"] = total
                metrics["capacity_used"] = used
                metrics["capacity_used_pct"] = round(used / total * 100, 2)
                if pool_count and compression:
                    metrics["data_reduction"] = round(compression / pool_count, 2)

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
                        ("oracle", data.get("purity_version",""),
                         data.get("capacity_total",0), data.get("capacity_used",0),
                         data.get("capacity_used_pct",0), data.get("data_reduction",1),
                         data.get("array_status","unknown"), data.get("controller_status","unknown"),
                         data["collected_at"], array_name),
                    )
                else:
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.metrics_current (
                            array_name, vendor, purity_version, capacity_total, capacity_used,
                            capacity_used_pct, data_reduction, array_status, controller_status, collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (array_name, "oracle", data.get("purity_version",""),
                         data.get("capacity_total",0), data.get("capacity_used",0),
                         data.get("capacity_used_pct",0), data.get("data_reduction",1),
                         data.get("array_status","unknown"), data.get("controller_status","unknown"),
                         data["collected_at"]),
                    )

                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, vendor, collected_at, capacity_total, capacity_used,
                        capacity_used_pct, data_reduction
                    ) VALUES (?, 'oracle', GETDATE(), ?, ?, ?, ?)""",
                    (array_name, data.get("capacity_total",0), data.get("capacity_used",0),
                     data.get("capacity_used_pct",0), data.get("data_reduction",1)),
                )

            result.records_saved = 2
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
