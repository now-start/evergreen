import os
from unittest.mock import Mock

import pytest

from evergreen.platform import bootstrap as bootstrap_module
from evergreen.platform.config import PlatformSettings


def test_bootstrap_loads_config_before_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_settings = PlatformSettings(spring_profiles_active="prod")
    runtime_settings = PlatformSettings(
        spring_profiles_active="prod",
        server_port=18080,
    )
    settings_loader = Mock(side_effect=[bootstrap_settings, runtime_settings])
    load_dotenv = Mock()
    load_spring_config = Mock()
    initialize_telemetry = Mock()
    monkeypatch.setattr(bootstrap_module, "get_settings", settings_loader)
    monkeypatch.setattr(bootstrap_module, "load_dotenv", load_dotenv)
    monkeypatch.setattr(bootstrap_module, "load_spring_config", load_spring_config)
    monkeypatch.setattr(bootstrap_module, "initialize_telemetry", initialize_telemetry)

    result = bootstrap_module.bootstrap_platform()

    assert result is runtime_settings
    load_dotenv.assert_called_once_with(override=False)
    assert settings_loader.cache_clear.call_count == 2
    load_spring_config.assert_called_once_with(bootstrap_settings)
    initialize_telemetry.assert_called_once_with(runtime_settings)


def test_telemetry_is_skipped_when_sdk_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize = Mock()
    monkeypatch.setattr(bootstrap_module, "initialize", initialize)

    bootstrap_module.initialize_telemetry(PlatformSettings(otel_sdk_disabled=True))

    initialize.assert_not_called()


def test_telemetry_is_skipped_for_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize = Mock()
    monkeypatch.setattr(bootstrap_module, "initialize", initialize)

    bootstrap_module.initialize_telemetry(PlatformSettings(spring_profiles_active="local"))

    initialize.assert_not_called()


def test_telemetry_initializes_after_remote_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize = Mock()
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(bootstrap_module, "initialize", initialize)

    bootstrap_module.initialize_telemetry(
        PlatformSettings(
            spring_application_name="evergreen",
            otel_sdk_disabled=False,
        )
    )

    assert os.environ["OTEL_SERVICE_NAME"] == "evergreen"
    initialize.assert_called_once_with(swallow_exceptions=False)


@pytest.mark.parametrize("configured_protocol", [None, "grpc", "http/protobuf"])
def test_telemetry_uses_spring_protocol_default_without_overriding_settings(
    monkeypatch: pytest.MonkeyPatch, configured_protocol: str | None
) -> None:
    environment = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example:4318",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector.example:4317",
    }
    if configured_protocol is not None:
        environment["OTEL_EXPORTER_OTLP_PROTOCOL"] = configured_protocol
    monkeypatch.setattr(os, "environ", environment.copy())
    observed_environment: dict[str, str] = {}
    initialize = Mock(side_effect=lambda **_: observed_environment.update(os.environ))
    monkeypatch.setattr(bootstrap_module, "initialize", initialize)

    bootstrap_module.initialize_telemetry(PlatformSettings())

    assert observed_environment == {
        **environment,
        "OTEL_SERVICE_NAME": "evergreen",
        "OTEL_EXPORTER_OTLP_PROTOCOL": configured_protocol or "http/protobuf",
    }
    initialize.assert_called_once_with(swallow_exceptions=False)
