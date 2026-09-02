import os

import httpx
import pytest

from evergreen.platform.config_server import SpringConfigError, load_spring_config
from evergreen.platform.settings import PlatformSettings


def test_spring_config_is_skipped_for_local_profile() -> None:
    settings = PlatformSettings(spring_profiles_active="local")

    assert load_spring_config(settings) == 0


def test_spring_config_loads_flat_properties_without_overriding_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVER_PORT", "18080")
    monkeypatch.delenv("MANAGEMENT_SERVER_PORT", raising=False)
    monkeypatch.delenv("EUREKA_CLIENT_SERVICE_URL_DEFAULT_ZONE", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)

    def config_response(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://config.example/evergreen-prod.json?resolvePlaceholders=true"
        )
        return httpx.Response(
            200,
            json={
                "server.port": 8080,
                "management.server.port": 8081,
                "eureka.client.serviceUrl.defaultZone": "http://eureka:8761/eureka/",
                "otel.sdk.disabled": False,
            },
        )

    settings = PlatformSettings(
        spring_profiles_active="prod",
        spring_config_import="configserver:https://config.example",
    )
    with httpx.Client(transport=httpx.MockTransport(config_response)) as client:
        loaded = load_spring_config(settings, client=client)

    assert loaded == 3
    assert os.environ["SERVER_PORT"] == "18080"
    assert os.environ["MANAGEMENT_SERVER_PORT"] == "8081"
    assert os.environ["EUREKA_CLIENT_SERVICE_URL_DEFAULT_ZONE"] == "http://eureka:8761/eureka/"
    assert os.environ["OTEL_SDK_DISABLED"] == "false"


def test_optional_spring_config_failure_does_not_block_startup() -> None:
    settings = PlatformSettings(
        spring_profiles_active="prod",
        spring_config_import="optional:configserver:https://config.example",
    )
    transport = httpx.MockTransport(lambda _: httpx.Response(503))

    with httpx.Client(transport=transport) as client:
        assert load_spring_config(settings, client=client) == 0


def test_required_spring_config_failure_blocks_startup() -> None:
    settings = PlatformSettings(
        spring_profiles_active="prod",
        spring_config_import="configserver:https://config.example",
    )
    transport = httpx.MockTransport(lambda _: httpx.Response(503))

    with httpx.Client(transport=transport) as client:
        with pytest.raises(SpringConfigError, match="unavailable"):
            load_spring_config(settings, client=client)
