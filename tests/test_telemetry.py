import os
from unittest.mock import Mock

import pytest

from evergreen.settings import PlatformSettings
from evergreen.telemetry import initialize_telemetry


def test_telemetry_is_skipped_when_sdk_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize = Mock()
    monkeypatch.setattr("evergreen.telemetry.initialize", initialize)

    initialize_telemetry(PlatformSettings(otel_sdk_disabled=True))

    initialize.assert_not_called()


def test_telemetry_is_skipped_for_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize = Mock()
    monkeypatch.setattr("evergreen.telemetry.initialize", initialize)

    initialize_telemetry(PlatformSettings(spring_profiles_active="local"))

    initialize.assert_not_called()


def test_telemetry_initializes_after_remote_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize = Mock()
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setattr("evergreen.telemetry.initialize", initialize)

    initialize_telemetry(
        PlatformSettings(
            spring_application_name="evergreen",
            otel_sdk_disabled=False,
        )
    )

    assert os.environ["OTEL_SERVICE_NAME"] == "evergreen"
    initialize.assert_called_once_with(swallow_exceptions=False)
