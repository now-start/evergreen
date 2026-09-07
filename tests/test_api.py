from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from evergreen import api as api_module
from evergreen.platform.config import PlatformSettings


@pytest.fixture
def app() -> FastAPI:
    return api_module.create_app(
        PlatformSettings(
            spring_profiles_active="local",
            server_port=8080,
            management_server_port=8081,
            otel_sdk_disabled=True,
        )
    )


@pytest_asyncio.fixture
async def application_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test:8080",
    ) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def management_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test:8081",
    ) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_endpoint_is_spring_actuator_compatible(
    management_client: AsyncClient,
) -> None:
    response = await management_client.get("/actuator/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UP"
    assert body["details"]["diskSpace"]["status"] == "UP"


@pytest.mark.asyncio
async def test_actuator_exposes_all_pyctuator_endpoints(
    management_client: AsyncClient,
) -> None:
    response = await management_client.get("/actuator/")

    assert response.status_code == 200
    links = response.json()["_links"]
    assert set(links) == {
        "self",
        "env",
        "info",
        "health",
        "metrics",
        "loggers",
        "dump",
        "threaddump",
        "logfile",
        "httptrace",
    }


@pytest.mark.asyncio
async def test_openapi_endpoint_matches_springdoc_path(
    application_client: AsyncClient,
) -> None:
    response = await application_client.get("/v3/api-docs")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"] == {
        "title": "evergreen API",
        "description": "evergreen service API",
        "version": "2.0.0a3",
    }
    assert schema["servers"] == [
        {"url": "/evergreen", "description": "Platform Gateway"},
        {"url": "/", "description": "Direct"},
    ]


@pytest.mark.asyncio
async def test_swagger_ui_uses_springdoc_compatible_path(
    application_client: AsyncClient,
) -> None:
    response = await application_client.get("/swagger-ui/index.html")

    assert response.status_code == 200
    assert "/v3/api-docs" in response.text
    assert "/swagger-ui/oauth2-redirect.html" in response.text


@pytest.mark.asyncio
async def test_lifespan_registers_and_deregisters_eureka(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = AsyncMock()
    register = AsyncMock(return_value=registration)
    deregister = AsyncMock()
    monkeypatch.setattr(api_module, "register_with_eureka", register)
    monkeypatch.setattr(api_module, "deregister_from_eureka", deregister)
    settings = PlatformSettings(spring_profiles_active="prod")
    app = api_module.create_app(settings)

    async with app.router.lifespan_context(app):
        register.assert_awaited_once_with(settings)

    deregister.assert_awaited_once_with(registration)


@pytest.mark.asyncio
async def test_application_and_management_ports_are_isolated(
    application_client: AsyncClient,
    management_client: AsyncClient,
) -> None:
    actuator_response = await application_client.get("/actuator/health")
    api_response = await management_client.get("/v3/api-docs")

    assert actuator_response.status_code == 404
    assert api_response.status_code == 404
