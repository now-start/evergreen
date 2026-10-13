import os
from unittest.mock import Mock, call

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
    initialize_database = Mock()
    ordered = Mock()
    ordered.attach_mock(load_spring_config, "config")
    ordered.attach_mock(initialize_database, "database")
    ordered.attach_mock(initialize_telemetry, "telemetry")
    monkeypatch.setattr(bootstrap_module, "get_settings", settings_loader)
    monkeypatch.setattr(bootstrap_module, "load_dotenv", load_dotenv)
    monkeypatch.setattr(bootstrap_module, "load_spring_config", load_spring_config)
    monkeypatch.setattr(bootstrap_module, "initialize_telemetry", initialize_telemetry)
    monkeypatch.setattr(bootstrap_module, "initialize_database", initialize_database)

    result = bootstrap_module.bootstrap_platform()

    assert result is runtime_settings
    load_dotenv.assert_called_once_with(override=False)
    assert settings_loader.cache_clear.call_count == 2
    load_spring_config.assert_called_once_with(bootstrap_settings)
    initialize_telemetry.assert_called_once_with(runtime_settings)
    assert ordered.mock_calls == [
        call.config(bootstrap_settings),
        call.telemetry(runtime_settings),
        call.database(runtime_settings),
    ]


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


def test_migration_failure_exports_logs_before_startup_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging
    from unittest.mock import AsyncMock

    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, InMemoryLogRecordExporter

    from evergreen import main as main_module

    settings = PlatformSettings(spring_profiles_active="prod")
    monkeypatch.setattr(bootstrap_module, "load_dotenv", Mock())
    monkeypatch.setattr(bootstrap_module, "load_spring_config", Mock())
    monkeypatch.setattr(bootstrap_module, "get_settings", Mock(return_value=settings))
    exporter = InMemoryLogRecordExporter()  # type: ignore[no-untyped-call]
    provider = LoggerProvider(shutdown_on_exit=False)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(exporter, schedule_delay_millis=60000)
    )
    handler = LoggingHandler(logger_provider=provider)
    root = logging.getLogger()
    monkeypatch.setattr(
        bootstrap_module, "initialize_telemetry", lambda _: root.addHandler(handler)
    )
    monkeypatch.setattr(bootstrap_module, "get_logger_provider", lambda: provider)
    monkeypatch.setattr(
        "evergreen.database.bootstrap.run_migrations",
        AsyncMock(side_effect=RuntimeError("secret-database-value")),
    )
    server = Mock()
    monkeypatch.setattr(main_module, "run_server", server)
    try:
        with pytest.raises(RuntimeError, match="서비스 기동 중단"):
            main_module.main()
        server.assert_not_called()
        bodies = [str(record.log_record.body) for record in exporter.get_finished_logs()]
        assert any(
            "event=database_migration status=failed error_type=RuntimeError" in body
            for body in bodies
        )
        assert not any("secret-database-value" in body for body in bodies)
    finally:
        root.removeHandler(handler)
        provider.shutdown()


@pytest.mark.parametrize("flush_result", [False, RuntimeError("exporter-secret")])
def test_flush_failure_preserves_migration_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    flush_result: bool | Exception,
) -> None:
    from opentelemetry.sdk._logs import LoggerProvider

    settings = PlatformSettings(spring_profiles_active="prod")
    monkeypatch.setattr(bootstrap_module, "load_dotenv", Mock())
    monkeypatch.setattr(bootstrap_module, "load_spring_config", Mock())
    monkeypatch.setattr(bootstrap_module, "get_settings", Mock(return_value=settings))
    monkeypatch.setattr(bootstrap_module, "initialize_telemetry", Mock())
    failure = ValueError("migration-failed")
    monkeypatch.setattr(bootstrap_module, "initialize_database", Mock(side_effect=failure))
    provider = Mock(spec=LoggerProvider)
    if isinstance(flush_result, Exception):
        provider.force_flush.side_effect = flush_result
    else:
        provider.force_flush.return_value = flush_result
    monkeypatch.setattr(bootstrap_module, "get_logger_provider", lambda: provider)
    with pytest.raises(ValueError) as caught:
        bootstrap_module.bootstrap_platform()
    assert caught.value is failure
    provider.force_flush.assert_called_once_with(timeout_millis=5000)
    assert "event=startup_log_flush" in caplog.text
    assert "exporter-secret" not in caplog.text


def test_startup_flush_skips_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = Mock()
    monkeypatch.setattr(bootstrap_module, "get_logger_provider", lambda: provider)
    bootstrap_module.flush_startup_logs()
    provider.force_flush.assert_not_called()
