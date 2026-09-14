import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from upbit import APIStatusError, APITimeoutError

from evergreen.database.connection import ExecutionLockError
from evergreen.trading import runtime
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader
from evergreen.trading.recovery import Recovery, TradingRejected, classify, delay
from evergreen.trading.state import StateUnavailable
from test_trading_execution import NOW, FakeUpbit, MemoryStore, settings


def timeout() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "https://api.upbit.com/v1/orders"))


@pytest.mark.parametrize(
    "status,minimum",
    [
        (401, 60),
        (403, 60),
        (404, 60),
        (429, 60),
        (418, 60),
        (408, 0),
        (503, 0),
        (400, 60),
    ],
)
def test_status_policy(status: int, minimum: float) -> None:
    error = APIStatusError(
        "private",
        response=httpx.Response(status, request=httpx.Request("GET", "https://api.upbit.com")),
        body=None,
    )
    policy = classify(error)
    assert policy.minimum_delay == minimum


def test_retry_after_and_backoff_caps() -> None:
    error = APIStatusError(
        "private",
        response=httpx.Response(
            429,
            headers={"Retry-After": "120"},
            request=httpx.Request("GET", "https://api.upbit.com"),
        ),
        body=None,
    )
    assert delay(1, classify(error)) == 120
    assert [delay(i, Recovery("test")) for i in range(1, 8)] == [
        5,
        10,
        20,
        40,
        60,
        60,
        60,
    ]
    assert delay(1000000, Recovery("test")) == 60


def test_all_errors_revalidate_with_static_reasons() -> None:
    assert classify(timeout()).reason == "exchange_connection"
    assert classify(ExecutionLockError("private")).reason == "database_lock_unavailable"
    assert (
        classify(OperationalError("private sql", {}, Exception(2013, "private"))).reason
        == "database_connection"
    )
    assert (
        classify(OperationalError("private sql", {}, Exception(1146, "private"))).reason
        == "database_error"
    )
    assert classify(ValueError("stale_quote")).reason == "revalidation_required"
    for reason in (
        "stale_quote",
        "settlement_locked",
        "expired_before_submission",
        "settlement_mismatch",
    ):
        assert classify(TradingRejected(reason, "private")).reason == reason


@pytest.mark.asyncio
async def test_common_loop_backoff_resets_only_after_a_successful_cycle(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0
    waits = []
    info = runtime.TradingRuntime()

    async def session(
        settings: TradingSettings,
        *,
        initialize: bool,
        once: bool,
        on_cycle: Callable[[str], None],
    ) -> int:
        nonlocal calls
        calls += 1
        if calls == 3:
            on_cycle("already-evaluated")
            assert info.info["reason"] is None and info.info["attempt"] == 0
        if calls < 4:
            raise timeout()
        raise asyncio.CancelledError

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(runtime, "_session", session)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    caplog.set_level(logging.INFO, logger="evergreen")
    with pytest.raises(asyncio.CancelledError):
        await runtime.run(
            settings(),
            initialize=False,
            once=False,
            on_cycle=info.record_cycle,
            on_recovery=info.record_recovery,
        )
    assert waits == [5, 10, 5]
    assert "worker_recovered" in caplog.text and "worker_recovering" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        StateUnavailable("execution_state_recovery_required"),
        TradingRejected("unexpected_balance", "private"),
        RuntimeError("private"),
    ],
)
async def test_every_failure_revalidates_and_automatically_resumes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    session = AsyncMock(side_effect=error)
    reports = []
    info = runtime.TradingRuntime()

    async def recovered(
        settings: TradingSettings,
        *,
        initialize: bool,
        once: bool,
        on_cycle: Callable[[str], None],
    ) -> int:
        on_cycle("no-signal")
        assert info.info["status"] == "running" and info.info["attempt"] == 0
        raise asyncio.CancelledError

    async def sleep(seconds: float) -> None:
        reports.append(seconds)
        assert info.info["status"] == "recovering" and info.info["retry_seconds"] == seconds
        if len(reports) == 2:
            session.side_effect = recovered

    monkeypatch.setattr(runtime, "_session", session)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    caplog.set_level(logging.INFO, logger="evergreen")
    with pytest.raises(asyncio.CancelledError):
        await runtime.run(
            settings(),
            initialize=False,
            once=False,
            on_recovery=info.record_recovery,
            on_cycle=info.record_cycle,
        )
    assert session.await_count == 3 and len(reports) == 2
    assert "private" not in caplog.text
    assert caplog.text.count("event=worker_recovering") == 2
    assert "event=worker_recovered" in caplog.text


@pytest.mark.asyncio
async def test_ambiguous_post_recovers_under_new_session_without_second_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, api = settings(), FakeUpbit()
    cfg.datasource_url = SecretStr("jdbc:mariadb://localhost/test")
    cfg.datasource_username = cfg.datasource_password = SecretStr("test")
    api.timeout = True
    store = MemoryStore(cfg.identity)
    resources = []
    close = AsyncMock()
    monkeypatch.setattr(api, "close", close)
    engines = [Mock(dispose=AsyncMock()), Mock(dispose=AsyncMock())]

    @asynccontextmanager
    async def opened(
        engine: AsyncEngine,
        identity: str,
        *,
        initialize: bool,
    ) -> AsyncIterator[MemoryStore]:
        resources.append("lock")
        try:
            yield store
        finally:
            resources.append("release")

    async def sleep(seconds: float) -> None:
        # The failed POST's session must be fully closed before retrying.
        assert resources == ["lock", "release"]
        assert close.await_count == 1 and engines[0].dispose.await_count == 1
        api.timeout = False
        api.order_state, api.cash, api.btc = "done", Decimal(1), Decimal(900)

    def completed(result: str) -> None:
        assert result == "reconciled"
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "create_async_engine", Mock(side_effect=engines))
    monkeypatch.setattr(runtime, "open_store", opened)
    monkeypatch.setattr(runtime, "Upbit", Mock(return_value=api))
    monkeypatch.setattr(
        runtime, "Trader", lambda api, store, settings: Trader(api, store, settings, lambda: NOW)
    )
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run(cfg, initialize=False, once=False, on_cycle=completed)
    assert len(api.sent) == 1 and store.state.pending is None
    assert resources == ["lock", "release", "lock", "release"]
    assert close.await_count == 2 and engines[1].dispose.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_interrupts_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    session = AsyncMock(side_effect=timeout())
    monkeypatch.setattr(runtime, "_session", session)
    task = asyncio.create_task(
        runtime.run(
            settings(), initialize=False, once=False, on_recovery=lambda *args: started.set()
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    session.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_cycle_diagnostic_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock(side_effect=timeout())
    monkeypatch.setattr(runtime, "_session", session)
    with pytest.raises(APITimeoutError):
        await runtime.run(settings(), initialize=False, once=True)
    session.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_error_does_not_mask_original_safety_failure() -> None:
    close = AsyncMock(side_effect=timeout())
    await runtime._cleanup(close, failed=True)
    with pytest.raises(APITimeoutError):
        await runtime._cleanup(close, failed=False)
