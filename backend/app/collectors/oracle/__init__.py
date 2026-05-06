# Import all collector modules so their @CollectorRegistry.register() decorators fire.
from app.collectors.oracle import metrics  # noqa: F401
from app.collectors.oracle import volumes  # noqa: F401
from app.collectors.oracle import alerts   # noqa: F401
