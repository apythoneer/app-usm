# Import all collector modules so their @CollectorRegistry.register() decorators fire.
from app.collectors.dell import metrics  # noqa: F401
from app.collectors.dell import volumes  # noqa: F401
from app.collectors.dell import alerts   # noqa: F401
