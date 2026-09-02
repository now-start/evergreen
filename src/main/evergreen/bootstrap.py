from dotenv import load_dotenv

from evergreen.config_server import load_spring_config
from evergreen.settings import PlatformSettings, get_settings
from evergreen.telemetry import initialize_telemetry


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
