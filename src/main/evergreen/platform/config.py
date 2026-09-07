import json
import logging
import os
import re
from collections.abc import Iterator
from functools import lru_cache
from urllib.parse import quote

import httpx
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_SERVER_URL = "http://localhost:8888"
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]+")


class PlatformSettings(BaseSettings):
    """Configuration shared with the Spring Platform."""

    spring_application_name: str = "evergreen"
    spring_profiles_active: str = "default"
    spring_config_import: str = "optional:configserver:https://spring.nowstart.org/config"

    server_address: str = "0.0.0.0"  # noqa: S104
    server_port: int = 8080
    management_server_port: int = 8081

    eureka_client_enabled: bool = True
    eureka_client_service_url_default_zone: str = "http://localhost:8761/eureka/"
    eureka_instance_hostname: str = ""

    otel_sdk_disabled: bool = False

    @property
    def active_profiles(self) -> set[str]:
        return {
            profile.strip().lower()
            for profile in self.spring_profiles_active.split(",")
            if profile.strip()
        }

    @property
    def platform_integrations_enabled(self) -> bool:
        return "local" not in self.active_profiles


class SpringConfigError(RuntimeError):
    """Raised when required Spring Config data cannot be loaded."""


@lru_cache
def get_settings() -> PlatformSettings:
    return PlatformSettings()


def load_spring_config(
    settings: PlatformSettings,
    *,
    client: httpx.Client | None = None,
) -> int:
    """Load flat or nested Spring Config properties into the process environment."""
    if not settings.platform_integrations_enabled:
        return 0

    optional, base_url = _parse_config_import(settings.spring_config_import)
    url = _build_config_url(base_url, settings)
    owns_client = client is None
    http_client = client or httpx.Client(timeout=5.0)

    try:
        response = http_client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as error:
        if optional:
            logger.warning("Optional Spring Config Server is unavailable: %s", error)
            return 0
        raise SpringConfigError("Spring Config Server is unavailable") from error
    finally:
        if owns_client:
            http_client.close()

    payload: object = response.json()
    if not isinstance(payload, dict):
        raise SpringConfigError("Spring Config Server response must be a JSON object")

    loaded = 0
    for property_name, value in _flatten_properties(payload):
        serialized = _serialize_property(value)
        if serialized is None:
            continue

        environment_name = _to_environment_name(property_name)
        if environment_name not in os.environ:
            os.environ[environment_name] = serialized
            loaded += 1

    return loaded


def _flatten_properties(
    properties: dict[str, object], prefix: str = ""
) -> Iterator[tuple[str, object]]:
    for key, value in properties.items():
        if not isinstance(key, str):
            raise SpringConfigError("Spring Config property names must be strings")
        property_name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _flatten_properties(value, property_name)
        else:
            yield property_name, value


def _parse_config_import(config_import: str) -> tuple[bool, str]:
    location = config_import.strip()
    optional = location.startswith("optional:")
    if optional:
        location = location.removeprefix("optional:")

    if not location.startswith("configserver:"):
        raise SpringConfigError("SPRING_CONFIG_IMPORT must use the configserver: scheme")

    base_url = location.removeprefix("configserver:").strip()
    return optional, base_url or _DEFAULT_CONFIG_SERVER_URL


def _build_config_url(base_url: str, settings: PlatformSettings) -> str:
    application = quote(settings.spring_application_name, safe="")
    profiles = settings.spring_profiles_active.strip() or "default"
    resource = f"{application}-{quote(profiles, safe=',')}.json"
    return f"{base_url.rstrip('/')}/{resource}?resolvePlaceholders=true"


def _to_environment_name(property_name: str) -> str:
    snake_case = _CAMEL_CASE_BOUNDARY.sub("_", property_name)
    return _NON_ALPHANUMERIC.sub("_", snake_case).strip("_").upper()


def _serialize_property(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str | int | float):
        return str(value)
    return json.dumps(value, separators=(",", ":"))
