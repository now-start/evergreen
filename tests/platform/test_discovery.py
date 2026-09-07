from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from evergreen.api import create_app
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_host", "url_host"),
    [("", "container-host"), ("evergreen", "evergreen"), ("::1", "[::1]")],
)
async def test_actuator_links_match_eureka_advertised_urls(
    monkeypatch: pytest.MonkeyPatch, configured_host: str, url_host: str
) -> None:
    monkeypatch.setattr("socket.gethostname", lambda: "container-host")
    init_async = AsyncMock()
    monkeypatch.setattr("evergreen.platform.discovery.eureka_client.init_async", init_async)
    settings = PlatformSettings(
        server_port=18080,
        management_server_port=18081,
        eureka_instance_hostname=configured_host,
    )
    app = create_app(settings)
    await register_with_eureka(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test:18081"
    ) as client:
        response = await client.get("/actuator/")
        assert response.status_code == 200
        links = response.json()["_links"]
        for name, link in links.items():
            suffix = "" if name == "self" else f"/{name}"
            assert link["href"] == f"http://{url_host}:18081/actuator{suffix}"
        assert (await client.get(links["info"]["href"])).status_code == 200
        assert (await client.get(links["metrics"]["href"])).status_code == 200

    assert init_async.await_args is not None
    registration = init_async.await_args.kwargs
    assert registration["home_page_url"] == f"http://{url_host}:18080"
    assert registration["status_page_url"] == links["info"]["href"]
    assert registration["health_check_url"] == links["health"]["href"]
