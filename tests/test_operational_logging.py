import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import SecretStr

from evergreen.observability import configure_logging, operation
from evergreen.platform.config import PlatformSettings, load_spring_config


def test_operation_logs_duration_without_exception_contents(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="evergreen")
    logger = logging.getLogger("evergreen.test")
    with operation(logger, "test_success", level=logging.INFO):
        pass
    with pytest.raises(ValueError, match="private-sentinel"):
        with operation(logger, "test_failure"):
            raise ValueError("private-sentinel")
    assert "event=test_success status=completed duration_ms=" in caplog.text
    assert "event=test_failure status=failed error_type=ValueError" in caplog.text
    assert "private-sentinel" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_configure_logging_preserves_handlers_and_honors_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    handlers = list(root.handlers)
    monkeypatch.setenv("LOGGING_LEVEL_EVERGREEN", "DEBUG")
    configure_logging()
    assert logging.getLogger("evergreen").level == logging.DEBUG
    assert root.handlers == handlers
    monkeypatch.setenv("LOGGING_LEVEL_EVERGREEN", "invalid")
    configure_logging()
    assert logging.getLogger("evergreen").level == logging.INFO


def test_config_logs_count_but_not_values(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "environ", {})
    caplog.set_level(logging.INFO, logger="evergreen")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"test.private.config": "private-sentinel"})
        )
    ) as client:
        assert load_spring_config(PlatformSettings(), client=client) == 1
    assert "event=config_loaded count=1" in caplog.text
    assert "private-sentinel" not in caplog.text


def test_optional_config_error_does_not_log_url(caplog: pytest.LogCaptureFixture) -> None:
    settings = PlatformSettings(
        spring_config_import="optional:configserver:https://example.invalid/private-sentinel"
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        assert load_spring_config(settings, client=client) == 0
    assert "event=config_unavailable" in caplog.text
    assert "private-sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_eureka_lifecycle_and_failure_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evergreen.platform.discovery import deregister_from_eureka, register_with_eureka

    caplog.set_level(logging.INFO, logger="evergreen")
    client = AsyncMock()
    init = AsyncMock(return_value=client)
    monkeypatch.setattr("evergreen.platform.discovery.eureka_client.init_async", init)
    assert await register_with_eureka(PlatformSettings()) is client
    await deregister_from_eureka(client)
    assert "event=eureka_client_initialize status=completed" in caplog.text
    assert "event=eureka_client_stop status=completed" in caplog.text
    init.side_effect = RuntimeError("private-sentinel")
    with pytest.raises(RuntimeError):
        await register_with_eureka(PlatformSettings())
    assert "event=eureka_client_initialize status=failed error_type=RuntimeError" in caplog.text
    assert "private-sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_worker_heartbeat_and_cleanup_without_external_io(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from evergreen.trading.config import TradingSettings
    from evergreen.trading.runtime import run

    @asynccontextmanager
    async def store(*args: object, **kwargs: object) -> AsyncIterator[object]:
        yield object()

    caplog.set_level(logging.INFO, logger="evergreen")
    engine = Mock(dispose=AsyncMock())
    api = Mock(close=AsyncMock())
    trader = Mock(tick=AsyncMock(return_value="pending"))
    monkeypatch.setattr("evergreen.trading.runtime.create_async_engine", Mock(return_value=engine))
    monkeypatch.setattr("evergreen.trading.runtime.open_store", store)
    monkeypatch.setattr("evergreen.trading.runtime.Upbit", Mock(return_value=api))
    monkeypatch.setattr("evergreen.trading.runtime.Trader", Mock(return_value=trader))
    monkeypatch.setattr("evergreen.trading.runtime.monotonic", Mock(side_effect=[0, 61, 61]))
    settings = TradingSettings(
        access_key=SecretStr("private-sentinel"),
        secret_key=SecretStr("private-sentinel"),
        datasource_url=SecretStr("jdbc:mariadb://localhost/test"),
        datasource_username=SecretStr("private-sentinel"),
        datasource_password=SecretStr("private-sentinel"),
    )
    assert await run(settings, initialize=False, once=True) == 0
    assert "event=worker_heartbeat result=pending" in caplog.text
    assert "event=worker_stopped" in caplog.text
    assert "private-sentinel" not in caplog.text
    api.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()
