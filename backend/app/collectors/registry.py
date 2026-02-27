"""
Collector Plugin Registry — auto-discovery and registration.

Vendors register their collectors with @CollectorRegistry.register().
The scheduler queries the registry to find all active collectors.

Adding a new vendor is as simple as:
    1. Create backend/app/collectors/netapp/__init__.py
    2. Import your collector modules there
    3. Decorate each with @CollectorRegistry.register("netapp", "metrics")
    4. Add the import to collectors/__init__.py
"""

import importlib
import logging
import pkgutil
from typing import Dict, List, Optional, Tuple, Type

from app.collectors.base import BaseCollector

logger = logging.getLogger("usm.registry")


class CollectorRegistry:
    # { vendor: { collector_type: CollectorClass } }
    _registry: Dict[str, Dict[str, Type[BaseCollector]]] = {}

    @classmethod
    def register(cls, vendor: str, collector_type: str):
        """
        Decorator to register a collector class.

        Usage:
            @CollectorRegistry.register("pure", "metrics")
            class PureMetricsCollector(BaseCollector): ...
        """
        def decorator(klass: Type[BaseCollector]) -> Type[BaseCollector]:
            if vendor not in cls._registry:
                cls._registry[vendor] = {}
            cls._registry[vendor][collector_type] = klass
            logger.info(f"Registered collector: {vendor}/{collector_type} → {klass.__name__}")
            return klass
        return decorator

    @classmethod
    def get(cls, vendor: str, collector_type: str) -> Optional[Type[BaseCollector]]:
        """Retrieve a registered collector class."""
        return cls._registry.get(vendor, {}).get(collector_type)

    @classmethod
    def all_registered(cls) -> List[Tuple[str, str, Type[BaseCollector]]]:
        """Return list of (vendor, collector_type, class) tuples."""
        result = []
        for vendor, types in cls._registry.items():
            for ctype, klass in types.items():
                result.append((vendor, ctype, klass))
        return result

    @classmethod
    def vendors(cls) -> List[str]:
        return list(cls._registry.keys())

    @classmethod
    def collector_types_for(cls, vendor: str) -> List[str]:
        return list(cls._registry.get(vendor, {}).keys())

    @classmethod
    def summary(cls) -> Dict:
        return {
            vendor: list(types.keys())
            for vendor, types in cls._registry.items()
        }


def load_all_collectors():
    """
    Auto-import all collector subpackages so their @register decorators fire.
    Call once at app startup.
    """
    import app.collectors as collectors_pkg

    for finder, name, ispkg in pkgutil.iter_modules(collectors_pkg.__path__):
        if ispkg and name not in ("__pycache__",):
            try:
                importlib.import_module(f"app.collectors.{name}")
                logger.info(f"Loaded collector package: {name}")
            except Exception as e:
                logger.warning(f"Could not load collector package '{name}': {e}")
