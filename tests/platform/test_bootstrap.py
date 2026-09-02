from unittest.mock import Mock

import pytest

from evergreen.platform import bootstrap as bootstrap_module
from evergreen.platform.settings import PlatformSettings


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
