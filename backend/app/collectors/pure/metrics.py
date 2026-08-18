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
from app.db.session import get_db_cursor, update_extended_metrics
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
            # Load/pressure indicator (Pure v1 REST has no controller CPU%).
            metrics["queue_depth"] = perf.get("queue_depth", 0)

        # ---- v2 metrics (best-effort; additive to v1) ---------------------------
        # over-subscription: provisioned vs usable
        sp = self.client.get_v2("arrays/space")
        if sp:
            space = (sp[0].get("space") or {})
            metrics["total_provisioned"] = space.get("total_provisioned")
        # latency breakdown (usec per op) — where latency originates
        v2perf = self.client.get_v2("arrays/performance")
        if v2perf:
            p = v2perf[0]

            def _avg(*keys):
                vals = [p.get(k) for k in keys if p.get(k) is not None]
                return round(sum(vals) / len(vals), 1) if vals else None

            metrics["san_latency_us"] = _avg("san_usec_per_read_op", "san_usec_per_write_op")
            metrics["queue_latency_us"] = _avg("queue_usec_per_read_op", "queue_usec_per_write_op")
        # NIC utilization + port errors — per-interface throughput vs link speed
        nperf = self.client.get_v2("network-interfaces/performance")
        nifs = self.client.get_v2("network-interfaces")
        if nperf and nifs:
            speed_by = {n["name"]: n.get("speed") for n in nifs if n.get("speed")}
            utils, errs = [], 0.0
            for n in nperf:
                for proto in ("eth", "fc"):
                    d = n.get(proto) or {}
                    rx = d.get("received_bytes_per_sec") or 0
                    tx = d.get("transmitted_bytes_per_sec") or 0
                    errs += sum(v for k, v in d.items() if "error" in k.lower() and isinstance(v, (int, float)))
                    spd = speed_by.get(n.get("name"))
                    if spd and (rx or tx):
                        utils.append(min(100.0, (rx + tx) * 8.0 / spd * 100.0))
            if utils:
                metrics["nic_util_pct"] = round(max(utils), 1)   # busiest interface
            metrics["nic_errors_per_sec"] = round(errs, 2)
        # hardware temperature (max across components)
        hw = self.client.get_v2("hardware")
        if hw:
            temps = [h.get("temperature") for h in hw if h.get("temperature") is not None]
            if temps:
                metrics["hw_temp_c"] = max(temps)

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

        # Uptime — Pure v1 API doesn't expose started/uptime on controllers.
        # Calculate from the last reboot alert in the messages table instead.
        try:
            from app.db.session import get_db_cursor as _get_cursor
            with _get_cursor() as cursor:
                cursor.execute(
                    f"""SELECT TOP 1 opened FROM {SCHEMA}.messages
                        WHERE array_name = ? AND vendor = 'pure'
                          AND (event LIKE '%reboot%' OR event LIKE '%restart%'
                               OR event LIKE '%power%cycle%')
                        ORDER BY opened DESC""",
                    (self.array_name,),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    metrics["last_reboot"] = str(row[0])
                    try:
                        reboot_str = str(row[0]).split("+")[0].split(".")[0].replace("T", " ")
                        reboot_dt = datetime.strptime(reboot_str[:19], "%Y-%m-%d %H:%M:%S")
                        uptime_secs = int((datetime.now() - reboot_dt).total_seconds())
                        if uptime_secs > 0:
                            metrics["uptime_seconds"] = uptime_secs
                            days = uptime_secs // 86400
                            hours = (uptime_secs % 86400) // 3600
                            metrics["uptime_str"] = f"{days}d {hours}h"
                    except Exception:
                        pass

                # Reboot count
                cursor.execute(
                    f"""SELECT COUNT(*) FROM {SCHEMA}.messages
                        WHERE array_name = ? AND vendor = 'pure'
                          AND (event LIKE '%reboot%' OR event LIKE '%restart%'
                               OR event LIKE '%power%cycle%')""",
                    (self.array_name,),
                )
                count_row = cursor.fetchone()
                if count_row:
                    metrics["reboot_count"] = count_row[0]
        except Exception as e:
            self.logger.debug(f"Uptime lookup error: {e}")

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
                            read_bandwidth=?, write_bandwidth=?, queue_depth=?,
                            capacity_total=?, capacity_used=?, capacity_used_pct=?,
                            data_reduction=?, total_reduction=?,
                            shared_space=?, snapshot_space=?, volume_space=?,
                            controller_status=?,
                            uptime_seconds=?, uptime_str=?, last_reboot=?, reboot_count=?,
                            collected_at=?
                        WHERE array_name=?""",
                        (
                            data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("queue_depth"),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1), data.get("total_reduction", 1),
                            data.get("shared_space", 0), data.get("snapshot_space", 0),
                            data.get("volume_space", 0),
                            data.get("controller_status", "unknown"),
                            data.get("uptime_seconds"), data.get("uptime_str"),
                            data.get("last_reboot"), data.get("reboot_count", 0),
                            data["collected_at"], array_name,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.metrics_current (
                            array_name, purity_version, read_iops, write_iops,
                            read_latency_us, write_latency_us, read_bandwidth, write_bandwidth,
                            queue_depth,
                            capacity_total, capacity_used, capacity_used_pct,
                            data_reduction, total_reduction, shared_space, snapshot_space,
                            volume_space, controller_status,
                            uptime_seconds, uptime_str, last_reboot, reboot_count,
                            collected_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            array_name, data.get("purity_version", ""),
                            data.get("read_iops", 0), data.get("write_iops", 0),
                            data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                            data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                            data.get("queue_depth"),
                            data.get("capacity_total", 0), data.get("capacity_used", 0),
                            data.get("capacity_used_pct", 0),
                            data.get("data_reduction", 1), data.get("total_reduction", 1),
                            data.get("shared_space", 0), data.get("snapshot_space", 0),
                            data.get("volume_space", 0),
                            data.get("controller_status", "unknown"),
                            data.get("uptime_seconds"), data.get("uptime_str"),
                            data.get("last_reboot"), data.get("reboot_count", 0),
                            data["collected_at"],
                        ),
                    )

                # Append to history for time-series
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.metrics_history (
                        array_name, collected_at,
                        read_latency_us, write_latency_us, read_iops, write_iops,
                        read_bandwidth, write_bandwidth, queue_depth,
                        nic_util_pct, total_provisioned, san_latency_us,
                        queue_latency_us, nic_errors_per_sec, hw_temp_c,
                        capacity_total, capacity_used, capacity_used_pct, data_reduction
                    ) VALUES (?, GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        array_name,
                        data.get("read_latency_us", 0), data.get("write_latency_us", 0),
                        data.get("read_iops", 0), data.get("write_iops", 0),
                        data.get("read_bandwidth", 0), data.get("write_bandwidth", 0),
                        data.get("queue_depth"),
                        data.get("nic_util_pct"), data.get("total_provisioned"),
                        data.get("san_latency_us"), data.get("queue_latency_us"),
                        data.get("nic_errors_per_sec"), data.get("hw_temp_c"),
                        data.get("capacity_total", 0), data.get("capacity_used", 0),
                        data.get("capacity_used_pct", 0), data.get("data_reduction", 1),
                    ),
                )

                # Extended metrics onto metrics_current (supplementary upsert)
                update_extended_metrics(cursor, SCHEMA, array_name, data)

            result.records_saved = 2  # current + history
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
