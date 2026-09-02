from functools import lru_cache

from pydantic_settings import BaseSettings


class PlatformSettings(BaseSettings):
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


@lru_cache
def get_settings() -> PlatformSettings:
    return PlatformSettings()
