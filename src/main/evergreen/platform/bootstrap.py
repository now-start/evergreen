import os

from dotenv import load_dotenv
from opentelemetry.instrumentation.auto_instrumentation import initialize

from evergreen.platform.config import PlatformSettings, get_settings, load_spring_config


def initialize_telemetry(settings: PlatformSettings) -> None:
    if not settings.platform_integrations_enabled or settings.otel_sdk_disabled:
        return

    os.environ.setdefault("OTEL_SERVICE_NAME", settings.spring_application_name)
    os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    initialize(swallow_exceptions=False)


def bootstrap_platform() -> PlatformSettings:
    """Load local overrides, Spring Config, validation, then telemetry."""
    load_dotenv(override=False)
    get_settings.cache_clear()
    bootstrap_settings = get_settings()
    load_spring_config(bootstrap_settings)
    get_settings.cache_clear()
    runtime_settings = get_settings()
    initialize_telemetry(runtime_settings)
    return runtime_settings
