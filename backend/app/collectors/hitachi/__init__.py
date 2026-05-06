# Import all collector modules so their @CollectorRegistry.register() decorators fire.
from app.collectors.hitachi import metrics  # noqa: F401
from app.collectors.hitachi import volumes  # noqa: F401
from app.collectors.hitachi import alerts   # noqa: F401
