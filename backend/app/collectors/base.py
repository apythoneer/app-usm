"""
Abstract Base Collector — the contract ALL vendor collectors must implement.

Every vendor creates a subclass per collector type (metrics, volumes, alerts)
and decorates it with @CollectorRegistry.register(vendor, collector_type).
The scheduler picks up all registered collectors automatically.
"""

import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.schemas.array import ArrayConfig


logger = logging.getLogger("usm.collectors")


class CollectorResult:
    """Standard result container returned by every collector."""

    def __init__(self, array_name: str, vendor: str, collector_type: str):
        self.array_name = array_name
        self.vendor = vendor
        self.collector_type = collector_type
        self.success = False
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.errors: List[str] = []
        self.records_saved: int = 0
        self.data: Dict[str, Any] = {}

    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "array_name": self.array_name,
            "vendor": self.vendor,
            "collector_type": self.collector_type,
            "success": self.success,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "records_saved": self.records_saved,
            "errors": self.errors,
        }


class BaseCollector(ABC):
    """
    Abstract base for all storage vendor collectors.

    Subclass this and implement authenticate(), collect(), save().
    Register with @CollectorRegistry.register(vendor, collector_type).

    Example:
        @CollectorRegistry.register("pure", "metrics")
        class PureMetricsCollector(BaseCollector):
            VENDOR = "pure"
            COLLECTOR_TYPE = "metrics"
            ...
    """

    VENDOR: str = "unknown"
    COLLECTOR_TYPE: str = "base"

    def __init__(self, array_config: ArrayConfig):
        self.array_config = array_config
        self.array_name = array_config.name
        self._setup_logger()

    def _setup_logger(self):
        log_dir = os.environ.get("LOG_DIR", "/app/logs/collectors")
        os.makedirs(log_dir, exist_ok=True)
        self.logger = logging.getLogger(
            f"usm.{self.VENDOR}.{self.COLLECTOR_TYPE}.{self.array_name}"
        )
        if not self.logger.handlers:
            fh = logging.FileHandler(
                os.path.join(log_dir, f"{self.VENDOR}_{self.COLLECTOR_TYPE}.log")
            )
            fh.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            self.logger.addHandler(fh)
            self.logger.setLevel(logging.INFO)

    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the storage array. Return True on success."""
        ...

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """Collect data from the array. Return collected data dict."""
        ...

    @abstractmethod
    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        """Persist collected data to DB. Populate result.records_saved."""
        ...

    def disconnect(self):
        """Optional cleanup — override if needed."""
        pass

    def _write_to_cache(self, data: Dict[str, Any]):
        """Write collected data to SQLite cache. Override in subclasses for custom logic."""
        from app.db.cache import upsert_metrics, get_cache_cursor
        import json

        if self.COLLECTOR_TYPE == "metrics":
            # Build metrics row from collected data
            cache_row = {
                "array_name": data.get("array_name", self.array_name),
                "vendor": data.get("vendor", self.VENDOR),
                "purity_version": data.get("purity_version") or data.get("firmware_version"),
                "read_latency_us": data.get("read_latency_us"),
                "write_latency_us": data.get("write_latency_us"),
                "read_iops": data.get("read_iops"),
                "write_iops": data.get("write_iops"),
                "read_bandwidth": data.get("read_bandwidth"),
                "write_bandwidth": data.get("write_bandwidth"),
                "capacity_total": data.get("capacity_total"),
                "capacity_used": data.get("capacity_used"),
                "capacity_used_pct": data.get("capacity_used_pct"),
                "data_reduction": data.get("data_reduction"),
                "total_reduction": data.get("total_reduction"),
                "shared_space": data.get("shared_space"),
                "snapshot_space": data.get("snapshot_space"),
                "volume_space": data.get("volume_space"),
                "array_status": data.get("array_status"),
                "controller_status": data.get("controller_status"),
                "network_status": data.get("network_status"),
                "uptime_seconds": data.get("uptime_seconds"),
                "uptime_str": data.get("uptime_str"),
                "last_reboot": data.get("last_reboot"),
                "reboot_count": data.get("reboot_count", 0),
                "collected_at": data.get("collected_at"),
            }
            upsert_metrics(cache_row)

        elif self.COLLECTOR_TYPE == "volumes":
            volumes = data.get("volumes", {})
            hosts = data.get("hosts", {})
            if not volumes and not hosts:
                return
            try:
                # Batched executemany — single transaction per table instead of
                # one round-trip per row (matches the SQL Server batch path).
                with get_cache_cursor() as cur:
                    if volumes:
                        cur.execute("DELETE FROM volumes_cache WHERE array_name=?", (self.array_name,))
                        vol_params = [
                            (self.array_name, self.VENDOR, v.get("volume_name", ""),
                             v.get("size", 0), v.get("used", 0),
                             v.get("data_reduction", 1), v.get("total_reduction"),
                             v.get("snapshots", 0), v.get("created", ""),
                             v.get("serial", ""),
                             json.dumps(v.get("hosts", [])),
                             json.dumps(v.get("host_groups", [])),
                             json.dumps(v.get("protection_groups", [])))
                            for v in volumes.values()
                        ]
                        cur.executemany(
                            """INSERT OR REPLACE INTO volumes_cache
                            (array_name, vendor, volume_name, size, used, data_reduction,
                             total_reduction, snapshots, created, serial, hosts, host_groups,
                             protection_groups, last_updated)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                            vol_params,
                        )
                    if hosts:
                        cur.execute("DELETE FROM hosts_cache WHERE array_name=?", (self.array_name,))
                        host_params = [
                            (self.array_name, self.VENDOR, h.get("host_name", ""),
                             h.get("wwn", ""), h.get("iqn", ""), h.get("nqn", ""),
                             h.get("host_group", ""),
                             json.dumps(h.get("volumes", [])))
                            for h in hosts.values()
                        ]
                        cur.executemany(
                            """INSERT OR REPLACE INTO hosts_cache
                            (array_name, vendor, host_name, wwn, iqn, nqn,
                             host_group, volumes, last_updated)
                            VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
                            host_params,
                        )
            except Exception as e:
                self.logger.debug(f"SQLite volumes/hosts cache failed: {e}")


    def run(self) -> CollectorResult:
        """Execute the full collection cycle. Called by the scheduler."""
        result = CollectorResult(self.array_name, self.VENDOR, self.COLLECTOR_TYPE)
        result.start_time = datetime.now()

        try:
            self.logger.info(f"Starting {self.COLLECTOR_TYPE} collection")

            if not self.authenticate():
                raise RuntimeError("Authentication failed")

            data = self.collect()
            if not data:
                raise RuntimeError("No data returned from collect()")

            # Apply configurable severity overrides to alert messages BEFORE save
            # so both the stored severity and Datadog/Teams paging reflect them
            # (e.g. Pure reports a controller reboot as warning; team wants critical).
            if self.COLLECTOR_TYPE == "alerts" and isinstance(data, dict) and data.get("messages"):
                try:
                    from app.collectors.alert_utils import apply_severity_overrides
                    n = apply_severity_overrides(data["messages"])
                    if n:
                        self.logger.info(f"applied severity override to {n} alert(s)")
                except Exception as e:
                    self.logger.warning(f"severity override failed (non-fatal): {e}")

            if not self.save(data, result):
                raise RuntimeError("save() returned False")

            # Dual-write to SQLite cache (non-fatal)
            try:
                self._write_to_cache(data)
            except Exception as ce:
                self.logger.debug(f"SQLite cache write skipped: {ce}")

            result.success = True
            self.logger.info(
                f"Collection complete — {result.records_saved} records saved"
            )

        except Exception as e:
            msg = str(e)
            result.errors.append(msg)
            self.logger.error(f"Collection failed: {msg}", exc_info=True)

        finally:
            self.disconnect()
            result.end_time = datetime.now()

        return result
