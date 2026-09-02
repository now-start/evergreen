import os

from opentelemetry.instrumentation.auto_instrumentation import initialize

from evergreen.platform.settings import PlatformSettings


def initialize_telemetry(settings: PlatformSettings) -> None:
    if not settings.platform_integrations_enabled or settings.otel_sdk_disabled:
        return

    os.environ.setdefault("OTEL_SERVICE_NAME", settings.spring_application_name)
    initialize(swallow_exceptions=False)
