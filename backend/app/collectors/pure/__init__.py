# Import all collector modules so their @CollectorRegistry.register() decorators fire.
# load_all_collectors() in registry.py imports this package, which triggers these imports.
from app.collectors.pure import metrics  # noqa: F401
from app.collectors.pure import volumes  # noqa: F401
from app.collectors.pure import alerts   # noqa: F401
