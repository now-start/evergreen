from unittest.mock import AsyncMock

import pytest

from evergreen.platform.config import PlatformSettings
from evergreen.platform.discovery import register_with_eureka


@pytest.mark.asyncio
async def test_eureka_registration_is_skipped_for_local_profile() -> None:
    settings = PlatformSettings(spring_profiles_active="local")

    assert await register_with_eureka(settings) is None


@pytest.mark.asyncio
async def test_eureka_registration_advertises_management_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eureka_client = object()
    init_async = AsyncMock(return_value=eureka_client)
    monkeypatch.setattr("evergreen.platform.discovery.eureka_client.init_async", init_async)
    settings = PlatformSettings(
        spring_profiles_active="prod",
        server_port=8080,
        management_server_port=8081,
        eureka_client_service_url_default_zone="http://eureka:8761/eureka/",
        eureka_instance_hostname="evergreen",
    )

    result = await register_with_eureka(settings)

    assert result is eureka_client
    init_async.assert_awaited_once_with(
        eureka_server="http://eureka:8761/eureka/",
        app_name="evergreen",
        instance_host="evergreen",
        instance_port=8080,
        home_page_url="http://evergreen:8080",
        status_page_url="http://evergreen:8081/actuator/info",
        health_check_url="http://evergreen:8081/actuator/health",
        metadata={"management.port": "8081"},
        strict_service_error_policy=False,
    )
