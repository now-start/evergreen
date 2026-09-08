import logging
from typing import Protocol, cast

from py_eureka_client import eureka_client  # type: ignore[import-untyped]

from evergreen.observability import operation
from evergreen.platform.config import PlatformSettings

logger = logging.getLogger(__name__)


class EurekaRegistration(Protocol):
    async def stop(self) -> None: ...


async def register_with_eureka(settings: PlatformSettings) -> EurekaRegistration | None:
    if not settings.platform_integrations_enabled or not settings.eureka_client_enabled:
        logger.info("event=eureka_skipped reason=disabled_or_local")
        return None

    with operation(logger, "eureka_client_initialize", level=logging.INFO):
        client = await eureka_client.init_async(
            eureka_server=settings.eureka_client_service_url_default_zone,
            app_name=settings.spring_application_name,
            instance_host=settings.advertised_host,
            instance_port=settings.server_port,
            home_page_url=settings.application_url,
            status_page_url=f"{settings.management_url}/actuator/info",
            health_check_url=f"{settings.management_url}/actuator/health",
            metadata={"management.port": str(settings.management_server_port)},
            strict_service_error_policy=False,
        )
    return cast(EurekaRegistration, client)


async def deregister_from_eureka(client: EurekaRegistration | None) -> None:
    if client is not None:
        with operation(logger, "eureka_client_stop", level=logging.INFO):
            await client.stop()
