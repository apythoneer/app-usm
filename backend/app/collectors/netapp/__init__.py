# Import all collector modules so their @CollectorRegistry.register() decorators fire.
# load_all_collectors() in registry.py imports this package, which triggers these imports.
from app.collectors.netapp import metrics  # noqa: F401
from app.collectors.netapp import volumes  # noqa: F401
from app.collectors.netapp import alerts   # noqa: F401
# StorageGrid uses same netapp package but registers as 'storagegrid' vendor
from app.collectors.netapp import storagegrid_metrics  # noqa: F401
