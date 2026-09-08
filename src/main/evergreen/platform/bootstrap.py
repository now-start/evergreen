import logging
import os

from dotenv import load_dotenv
from opentelemetry.instrumentation.auto_instrumentation import initialize

from evergreen.database.bootstrap import initialize_database
from evergreen.observability import configure_logging, operation
from evergreen.platform.config import PlatformSettings, get_settings, load_spring_config

logger = logging.getLogger(__name__)


def initialize_telemetry(settings: PlatformSettings) -> None:
    if not settings.platform_integrations_enabled or settings.otel_sdk_disabled:
        logger.info("event=telemetry_skipped reason=disabled_or_local")
        return

    os.environ.setdefault("OTEL_SERVICE_NAME", settings.spring_application_name)
    os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    with operation(logger, "telemetry_initialize", level=logging.INFO):
        initialize(swallow_exceptions=False)


def bootstrap_platform() -> PlatformSettings:
    """Load Spring Config, migrate the database, then initialize telemetry."""
    load_dotenv(override=False)
    configure_logging()
    get_settings.cache_clear()
    bootstrap_settings = get_settings()
    logger.info("event=bootstrap_started profiles=%s", sorted(bootstrap_settings.active_profiles))
    with operation(logger, "config_load", level=logging.INFO):
        load_spring_config(bootstrap_settings)
    configure_logging()
    get_settings.cache_clear()
    runtime_settings = get_settings()
    initialize_database(runtime_settings)
    initialize_telemetry(runtime_settings)
    logger.info("event=bootstrap_completed profiles=%s", sorted(runtime_settings.active_profiles))
    return runtime_settings
