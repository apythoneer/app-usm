"""
HPE 3Par / Primera / Alletra Metrics Collector
Collects performance + capacity metrics from WSAPI REST API.
Saves to metrics_current + metrics_history (same tables as Pure/NetApp).
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.hpe.client import HPEClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.hpe.metrics")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("hpe", "metrics")
class HPEMetricsCollector(BaseCollector):
    VENDOR = "hpe"
    COLLECTOR_TYPE = "metrics"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"HPE array '{array_config.name}' has no cred_key configured")
        self.client = HPEClient(
            array_name=array_config.name,
            cred_key=cred_key,
            fqdn=array_config.array_fqdn,
            model=array_config.model,
            mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {
            "array_name": self.array_name,
            "vendor": "hpe",
            "collected_at": datetime.now().isoformat(),
        }

        # System info — model, serial, firmware, capacity
        data = self.client.get("system")
        if data:
            metrics["purity_version"] = data.get("systemVersion", "")
            metrics["array_status"] = "healthy"
            metrics["controller_status"] = "healthy"

            # Capacity from system (MiB → bytes)
            total_mib = data.get("totalCapacityMiB", 0)
            alloc_mib = data.get("allocatedCapacityMiB", 0)
            free_mib = data.get("freeCapacityMiB", 0)
            if total_mib:
                metrics["capacity_total"] = int(total_mib * 1048576)  # MiB → bytes
                metrics["capacity_used"] = int(alloc_mib * 1048576)
                metrics["capacity_used_pct"] = round(alloc_mib / total_mib * 100, 2) if total_mib else 0

            # Node count for uptime tracking
            metrics["uptime_str"] = f"{data.get('totalNodes', 0)} nodes"

        # Overall system statistics for IOPS/latency
        stat_data = self.client.get("systemreporter/attime/cachestats")
        if stat_data and stat_data.get("members"):
            members = stat_data["members"]
            if members:
                latest = members[-1] if isinstance(members, list) else members
                metrics["read_iops"] = latest.get("readHits", 0)
                metrics["write_iops"] = latest.get("writeHits", 0)

        # Try alternative performance stats
        perf_data = self.client.get("systemreporter/attime/portstatisticsbyport")
        if perf_data and perf_data.get("members"):
            total_read_iops = 0
            total_write_iops = 0
            total_read_bw = 0
            total_write_bw = 0
            for port in perf_data["members"]:
                total_read_iops += port.get("readIO", 0)
                total_write_iops += port.get("writeIO", 0)
                total_read_bw += port.get("readKBytes", 0) * 1024
                total_write_bw += port.get("writeKBytes", 0) * 1024
            if total_read_iops or total_write_iops:
                metrics["read_iops"] = total_read_iops
                metrics["write_iops"] = total_write_iops
                metrics["read_bandwidth"] = total_read_bw
                metrics["write_bandwidth"] = total_write_bw

        # CPG (Common Provisioning Groups) for data reduction
        cpg_data = self.client.get("cpgs")
        if cpg_data and cpg_data.get("members"):
            total_logical = 0
            total_raw = 0
            for cpg in cpg_data["members"]:
                sa = cpg.get("SAUsage", {})
                sd = cpg.get("SDUsage", {})
                total_raw += sa.get("rawTotalMiB", 0) + sd.get("rawTotalMiB", 0)
                total_logical += sa.get("totalMiB", 0) + sd.get("totalMiB", 0)
            if total_raw and total_logical:
                metrics["data_reduction"] = round(total_logical / total_raw, 2) if total_raw else 1

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
                cursor.execute(
                    f"SELECT id FROM {SCHEMA}.metrics_current WHERE array_name = ?",
                    (array_name,),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute(
                        f"""UPDATE {SCHEMA}.metrics_current SET
                            vendor=?, purity_version=?, read_iops=?, write_iops=?,
                            read_latency_us=?, write_latency_us=?,
                            read_bandwidth=?, write_bandwidth=?,
                            capacity_total=?, capacity_used=?, capacity_used_pct=?,
                            data_reduction=?,
                            array_status=?, controller_status=?,
                            uptime_str=?,
                            collected_at=?
                        WHERE array_name=?""",
                        (
                            "hpe",
                            data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1),
                            data.get("array_status", "unknown"),
                            data.get("controller_status", "unknown"),
                            data.get("uptime_str"),
                            data["collected_at"], array_name,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.metrics_current (
                            array_name, vendor, purity_version, read_iops, write_iops,
                            read_latency_us, write_latency_us, read_bandwidth, write_bandwidth,
                            capacity_total, capacity_used, capacity_used_pct,
                            data_reduction,
                            array_status, controller_status,
                            uptime_str,
                            collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            array_name, "hpe",
                            data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1),
                            data.get("array_status", "unknown"),
                            data.get("controller_status", "unknown"),
                            data.get("uptime_str"),
                            data["collected_at"],
                        ),
                    )

                # History
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, vendor, collected_at,
                        read_latency_us, write_latency_us, read_iops, write_iops,
                        read_bandwidth, write_bandwidth,
                        capacity_total, capacity_used, capacity_used_pct, data_reduction
                    ) VALUES (?, 'hpe', GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        array_name,
                        data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                        data.get("read_iops", 0), data.get("write_iops", 0),
                        data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                        data.get("capacity_total", 0), data.get("capacity_used", 0),
                        data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                    ),
                )

            result.records_saved = 2
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
