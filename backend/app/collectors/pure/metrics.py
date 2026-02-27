"""
Pure Storage Metrics Collector v2
Collects performance + capacity metrics, saves to metrics_current + metrics_history.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.pure.client import PureClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.pure.metrics")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("pure", "metrics")
class PureMetricsCollector(BaseCollector):
    VENDOR = "pure"
    COLLECTOR_TYPE = "metrics"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        self.client = PureClient(array_config.name)

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {
            "array_name": self.array_name,
            "vendor": "pure",
            "collected_at": datetime.now().isoformat(),
        }

        # Array info / firmware
        data = self.client.get("array")
        if data:
            arr = data[0] if isinstance(data, list) else data
            metrics["purity_version"] = arr.get("version", "")

        # Performance
        data = self.client.get("array", params={"action": "monitor"})
        if data:
            perf = data[0] if isinstance(data, list) else data
            metrics["read_iops"] = perf.get("reads_per_sec", 0)
            metrics["write_iops"] = perf.get("writes_per_sec", 0)
            metrics["read_latency_us"] = perf.get("usec_per_read_op", 0)
            metrics["write_latency_us"] = perf.get("usec_per_write_op", 0)
            metrics["read_bandwidth"] = perf.get("input_per_sec", 0)
            metrics["write_bandwidth"] = perf.get("output_per_sec", 0)

        # Capacity
        data = self.client.get("array", params={"space": "true"})
        if data:
            space = data[0] if isinstance(data, list) else data
            total = space.get("capacity", 0)
            used = space.get("total", 0)
            metrics["capacity_total"] = total
            metrics["capacity_used"] = used
            metrics["capacity_used_pct"] = round(used / total * 100, 2) if total else 0
            metrics["data_reduction"] = space.get("data_reduction", 1)
            metrics["total_reduction"] = space.get("total_reduction", 1)
            metrics["shared_space"] = space.get("shared_space", 0)
            metrics["snapshot_space"] = space.get("snapshots", 0)
            metrics["volume_space"] = space.get("volumes", 0)

        # Controller status
        data = self.client.get("array", params={"controllers": "true"})
        if data:
            statuses = [c.get("status", "unknown") for c in data]
            metrics["controller_status"] = (
                "healthy" if all(s == "ready" for s in statuses) else "degraded"
            )

        return metrics

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        array_name = data["array_name"]
        try:
            with get_db_cursor() as cursor:
                # Upsert metrics_current
                cursor.execute(
                    f"SELECT id FROM {SCHEMA}.metrics_current WHERE array_name = ?",
                    (array_name,),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute(
                        f"""UPDATE {SCHEMA}.metrics_current SET
                            purity_version=?, read_iops=?, write_iops=?,
                            read_latency_us=?, write_latency_us=?,
                            read_bandwidth=?, write_bandwidth=?,
                            capacity_total=?, capacity_used=?, capacity_used_pct=?,
                            data_reduction=?, total_reduction=?,
                            shared_space=?, snapshot_space=?, volume_space=?,
                            controller_status=?, collected_at=?
                        WHERE array_name=?""",
                        (
                            data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1), data.get("total_reduction", 1),
                            data.get("shared_space", 0), data.get("snapshot_space", 0),
                            data.get("volume_space", 0),
                            data.get("controller_status", "unknown"),
                            data["collected_at"], array_name,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.metrics_current (
                            array_name, purity_version, read_iops, write_iops,
                            read_latency_us, write_latency_us, read_bandwidth, write_bandwidth,
                            capacity_total, capacity_used, capacity_used_pct,
                            data_reduction, total_reduction, shared_space, snapshot_space,
                            volume_space, controller_status, collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            array_name, data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1), data.get("total_reduction", 1),
                            data.get("shared_space", 0), data.get("snapshot_space", 0),
                            data.get("volume_space", 0),
                            data.get("controller_status", "unknown"),
                            data["collected_at"],
                        ),
                    )

                # Append to history for time-series
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, collected_at,
                        read_latency_us, write_latency_us, read_iops, write_iops,
                        read_bandwidth, write_bandwidth,
                        capacity_total, capacity_used, capacity_used_pct, data_reduction
                    ) VALUES (?, GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        array_name,
                        data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                        data.get("read_iops", 0), data.get("write_iops", 0),
                        data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                        data.get("capacity_total", 0), data.get("capacity_used", 0),
                        data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                    ),
                )

            result.records_saved = 2  # current + history
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
