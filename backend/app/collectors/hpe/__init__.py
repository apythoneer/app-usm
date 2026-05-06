# Import all collector modules so their @CollectorRegistry.register() decorators fire.
from app.collectors.hpe import metrics  # noqa: F401
from app.collectors.hpe import volumes  # noqa: F401
from app.collectors.hpe import alerts   # noqa: F401
