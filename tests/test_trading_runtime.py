import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.utils import is_instrumentation_enabled
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from evergreen import api as api_module
from evergreen.platform.config import PlatformSettings
from evergreen.trading import runtime
from evergreen.trading.config import TradingSettings
from evergreen.trading.state import Store
from evergreen.trading.upbit import Upbit


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "TradingSettings", lambda: TradingSettings(live_enabled=True))
    monkeypatch.setattr(TradingSettings, "require_credentials", lambda _: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,enabled", [("default", False), ("local", True)])
async def test_disabled_runtime_does_not_start_worker(
    monkeypatch: pytest.MonkeyPatch, profile: str, enabled: bool
) -> None:
    monkeypatch.setattr(runtime, "TradingSettings", lambda: TradingSettings(live_enabled=enabled))
    run = AsyncMock()
    monkeypatch.setattr(runtime, "run", run)
    worker = runtime.TradingRuntime()
    async with worker.lifespan(PlatformSettings(spring_profiles_active=profile)):
        assert worker.info["status"] == "disabled"
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_lifespan_runs_worker_and_awaits_cleanup(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    async def run(
        settings: TradingSettings,
        *,
        initialize: bool,
        once: bool,
        on_cycle: Callable[[str], None],
    ) -> int:
        assert not initialize and not once
        on_cycle("no-signal")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return 0

    monkeypatch.setattr(runtime, "run", run)
    register, deregister = AsyncMock(), AsyncMock()
    monkeypatch.setattr(api_module, "register_with_eureka", register)
    monkeypatch.setattr(api_module, "deregister_from_eureka", deregister)
    app = api_module.create_app(PlatformSettings())
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(started.wait(), timeout=1)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test:8081"
        ) as client:
            response = await client.get("/actuator/info")
            assert response.status_code == 200
            trading = response.json()["trading"]
            assert trading["status"] == "running"
            assert trading["last_result"] == "no-signal"
            assert trading["last_cycle_at"]
            assert (await client.get("/actuator/health")).status_code == 200
    assert stopped.is_set()
    deregister.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_failure_is_visible_without_retry_or_secret_logging(
    configured: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    run = AsyncMock(side_effect=RuntimeError("private-order-payload"))
    monkeypatch.setattr(runtime, "run", run)
    worker = runtime.TradingRuntime()
    with caplog.at_level(logging.INFO, logger="evergreen"):
        async with worker.lifespan(PlatformSettings()):
            for _ in range(10):
                await asyncio.sleep(0)
                if worker.info["status"] == "failed":
                    break
            assert worker.info["status"] == "failed"
            assert worker.info["error_type"] == "RuntimeError"
        run.assert_awaited_once()
    assert "private-order-payload" not in caplog.text
    assert "worker_failed" in caplog.text
    assert worker.info["status"] == "failed"


@pytest.mark.asyncio
async def test_invalid_enabled_configuration_is_reported_without_starting_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime, "TradingSettings", Mock(side_effect=ValueError("private-configuration"))
    )
    run = AsyncMock()
    monkeypatch.setattr(runtime, "run", run)
    worker = runtime.TradingRuntime()
    async with worker.lifespan(PlatformSettings()):
        assert worker.info["status"] == "failed"
        assert worker.info["error_type"] == "ValueError"
    run.assert_not_awaited()


def test_halted_cycle_is_distinct_from_running() -> None:
    worker = runtime.TradingRuntime()
    worker.record_cycle("halted")
    assert worker.info["status"] == "halted"
    assert worker.info["last_result"] == "halted"


@pytest.mark.asyncio
async def test_shared_loop_cancellation_cleans_resources_and_isolates_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = TradingSettings(
        live_enabled=True,
        access_key=SecretStr("test-access"),
        secret_key=SecretStr("s" * 64),
        datasource_url=SecretStr("jdbc:mariadb://localhost/test"),
        datasource_username=SecretStr("test"),
        datasource_password=SecretStr("test"),
    )
    engine = AsyncMock(spec=AsyncEngine)
    closed, started = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def store(db: AsyncEngine, identity: str, *, initialize: bool) -> AsyncIterator[Store]:
        assert not initialize
        assert not is_instrumentation_enabled()
        try:
            yield cast(Store, Mock())
        finally:
            closed.set()

    def exchange_response(request: Request) -> Response:
        return Response(
            200,
            json={
                "uuid": "test-uuid",
                "identifier": "private-order-identifier",
                "market": "KRW-BTC",
                "side": "bid",
                "state": "wait",
                "executed_volume": "0",
                "paid_fee": "0",
            },
        )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentor = HTTPXClientInstrumentor()
    exchange = Upbit(config, transport=MockTransport(exchange_response))
    instrumentor.instrument_client(exchange.sdk._client, tracer_provider=provider)

    async def tick() -> str:
        await exchange.order("private-order-identifier")
        started.set()
        await asyncio.Event().wait()
        return "no-signal"

    monkeypatch.setattr(runtime, "create_async_engine", Mock(return_value=engine))
    monkeypatch.setattr(runtime, "open_store", store)
    monkeypatch.setattr(runtime, "Upbit", Mock(return_value=exchange))
    monkeypatch.setattr(runtime, "Trader", Mock(return_value=Mock(tick=tick)))
    task = asyncio.create_task(runtime.run(config, initialize=False, once=False))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert is_instrumentation_enabled()  # Other API tasks retain normal telemetry.
        async with AsyncClient(transport=MockTransport(exchange_response)) as client:
            instrumentor.instrument_client(client, tracer_provider=provider)
            await client.get("https://example.test/public")
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert "private-order-identifier" not in str(spans[0].attributes)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        provider.shutdown()
    assert exchange.sdk.is_closed()
    assert closed.is_set()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_loop_return_is_failure_without_restart(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = AsyncMock(return_value=0)
    monkeypatch.setattr(runtime, "run", run)
    worker = runtime.TradingRuntime()
    async with worker.lifespan(PlatformSettings()):
        await asyncio.sleep(0)
        assert worker.info["status"] == "failed"
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_credentials_block_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "TradingSettings", lambda: TradingSettings(live_enabled=True))
    run = AsyncMock()
    monkeypatch.setattr(runtime, "run", run)
    worker = runtime.TradingRuntime()
    async with worker.lifespan(PlatformSettings()):
        assert worker.info["status"] == "failed"
    run.assert_not_awaited()
