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

            if not self.save(data, result):
                raise RuntimeError("save() returned False")

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
