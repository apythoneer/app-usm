"""
NetApp ONTAP Metrics Collector
Collects performance + capacity metrics from ONTAP REST API.
Saves to metrics_current + metrics_history (same tables as Pure).
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.netapp.client import NetAppClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.netapp.metrics")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("netapp", "metrics")
class NetAppMetricsCollector(BaseCollector):
    VENDOR = "netapp"
    COLLECTOR_TYPE = "metrics"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"NetApp array '{array_config.name}' has no cred_key configured")
        self.client = NetAppClient(array_config.name, cred_key)

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {
            "array_name": self.array_name,
            "vendor": "netapp",
            "collected_at": datetime.now().isoformat(),
        }

        # Cluster info — version + name
        data = self.client.get("cluster")
        if data:
            ver = data.get("version", {})
            full_ver = ver.get("full", "")
            metrics["purity_version"] = full_ver[:50] if full_ver else ""  # column is NVARCHAR(50)

        # Cluster performance metrics — use cluster-level metric embedded in /cluster
        data = self.client.get("cluster", params={"fields": "metric"})
        if data:
            metric = data.get("metric", {})
            if metric:
                iops = metric.get("iops", {})
                latency = metric.get("latency", {})
                throughput = metric.get("throughput", {})
                metrics["read_iops"] = iops.get("read", 0)
                metrics["write_iops"] = iops.get("write", 0)
                # ONTAP latency is in microseconds already
                metrics["read_latency_us"] = latency.get("read", 0)
                metrics["write_latency_us"] = latency.get("write", 0)
                metrics["read_bandwidth"] = throughput.get("read", 0)
                metrics["write_bandwidth"] = throughput.get("write", 0)

        # Capacity — sum all aggregates
        aggs = self.client.get_all("storage/aggregates", params={
            "fields": "space",
        })
        if aggs:
            total = 0
            used = 0
            for agg in aggs:
                space = agg.get("space", {})
                block = space.get("block_storage", {})
                # Try block_storage first (newer ONTAP), fall back to top-level space
                agg_size = block.get("size") or space.get("size", 0)
                agg_used = block.get("used") or space.get("used", 0)
                total += agg_size or 0
                used += agg_used or 0

                # Data reduction from efficiency stats
                efficiency = space.get("efficiency", {})
                if not efficiency:
                    efficiency = space.get("efficiency_without_snapshots", {})
            metrics["capacity_total"] = total
            metrics["capacity_used"] = used
            metrics["capacity_used_pct"] = round(used / total * 100, 2) if total else 0

        # Data reduction — from aggregate efficiency ratios
        if aggs:
            ratios = []
            for agg in aggs:
                space = agg.get("space", {})
                # Try multiple locations for the ratio
                ratio = (
                    space.get("efficiency", {}).get("ratio")
                    or space.get("efficiency_without_snapshots", {}).get("ratio")
                )
                if ratio:
                    try:
                        ratios.append(float(ratio))
                    except (ValueError, TypeError):
                        pass
            if ratios:
                metrics["data_reduction"] = round(sum(ratios) / len(ratios), 2)

        # Node-level health + uptime
        # ONTAP /api/cluster does NOT have a top-level "health" boolean;
        # health is reported per-node via /api/cluster/nodes.
        data = self.client.get("cluster/nodes")
        if data:
            records = data.get("records", [])
            if records:
                node_health = []
                node_uptimes = []
                for n in records:
                    h = n.get("health", n.get("is_healthy"))
                    node_health.append(h)
                    # ONTAP nodes have "uptime" in seconds (when requested via fields)
                    uptime = n.get("uptime")
                    if uptime:
                        node_uptimes.append(int(uptime))

                if any(h is not None for h in node_health):
                    all_nodes_ok = all(h is True for h in node_health)
                    metrics["controller_status"] = "healthy" if all_nodes_ok else "degraded"
                    metrics["array_status"] = "healthy" if all_nodes_ok else "degraded"
                else:
                    metrics["controller_status"] = "healthy"
                    metrics["array_status"] = "healthy"

                # Use minimum node uptime as cluster uptime (most recent reboot)
                if node_uptimes:
                    uptime_secs = min(node_uptimes)
                    metrics["uptime_seconds"] = uptime_secs
                    days = uptime_secs // 86400
                    hours = (uptime_secs % 86400) // 3600
                    metrics["uptime_str"] = f"{days}d {hours}h"
            else:
                self.logger.warning("No node records returned from /cluster/nodes")
        else:
            self.logger.warning("Failed to fetch /cluster/nodes")

        # Try to get uptime via a separate call with explicit fields if not already captured
        if "uptime_seconds" not in metrics:
            node_data = self.client.get("cluster/nodes", params={"fields": "uptime"})
            if node_data:
                records = node_data.get("records", [])
                uptimes = [int(n["uptime"]) for n in records if n.get("uptime")]
                if uptimes:
                    uptime_secs = min(uptimes)
                    metrics["uptime_seconds"] = uptime_secs
                    days = uptime_secs // 86400
                    hours = (uptime_secs % 86400) // 3600
                    metrics["uptime_str"] = f"{days}d {hours}h"

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
                            vendor=?, purity_version=?, read_iops=?, write_iops=?,
                            read_latency_us=?, write_latency_us=?,
                            read_bandwidth=?, write_bandwidth=?,
                            capacity_total=?, capacity_used=?, capacity_used_pct=?,
                            data_reduction=?,
                            array_status=?, controller_status=?,
                            uptime_seconds=?, uptime_str=?, last_reboot=?,
                            collected_at=?
                        WHERE array_name=?""",
                        (
                            "netapp",
                            data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1),
                            data.get("array_status", "unknown"),
                            data.get("controller_status", "unknown"),
                            data.get("uptime_seconds"), data.get("uptime_str"),
                            data.get("last_reboot"),
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
                            uptime_seconds, uptime_str, last_reboot,
                            collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            array_name, "netapp",
                            data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1),
                            data.get("array_status", "unknown"),
                            data.get("controller_status", "unknown"),
                            data.get("uptime_seconds"), data.get("uptime_str"),
                            data.get("last_reboot"),
                            data["collected_at"],
                        ),
                    )

                # Append to history for time-series
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, vendor, collected_at,
                        read_latency_us, write_latency_us, read_iops, write_iops,
                        read_bandwidth, write_bandwidth,
                        capacity_total, capacity_used, capacity_used_pct, data_reduction
                    ) VALUES (?, 'netapp', GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
