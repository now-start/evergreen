import socket
from typing import Protocol, cast

from py_eureka_client import eureka_client  # type: ignore[import-untyped]

from evergreen.platform.config import PlatformSettings


class EurekaRegistration(Protocol):
    async def stop(self) -> None: ...


async def register_with_eureka(settings: PlatformSettings) -> EurekaRegistration | None:
    if not settings.platform_integrations_enabled or not settings.eureka_client_enabled:
        return None

    advertised_host = settings.eureka_instance_hostname or socket.gethostname()
    application_url = _instance_url(advertised_host, settings.server_port)
    management_url = _instance_url(advertised_host, settings.management_server_port)

    client = await eureka_client.init_async(
        eureka_server=settings.eureka_client_service_url_default_zone,
        app_name=settings.spring_application_name,
        instance_host=advertised_host,
        instance_port=settings.server_port,
        home_page_url=application_url,
        status_page_url=f"{management_url}/actuator/info",
        health_check_url=f"{management_url}/actuator/health",
        metadata={"management.port": str(settings.management_server_port)},
        strict_service_error_policy=False,
    )
    return cast(EurekaRegistration, client)


async def deregister_from_eureka(client: EurekaRegistration | None) -> None:
    if client is not None:
        await client.stop()


def _instance_url(host: str, port: int) -> str:
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{url_host}:{port}"
