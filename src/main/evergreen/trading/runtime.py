"""Trading loop owned by the API lifespan, with explicit failure and shutdown state."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic

from opentelemetry.instrumentation.utils import suppress_instrumentation
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.platform.config import PlatformSettings
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader
from evergreen.trading.state import StateUnavailable, open_store
from evergreen.trading.upbit import Upbit

logger = logging.getLogger(__name__)


async def run(
    settings: TradingSettings,
    *,
    initialize: bool,
    once: bool,
    on_cycle: Callable[[str], None] | None = None,
) -> int:
    if initialize and settings.live_enabled:
        raise StateUnavailable("initialization_requires_live_disabled")
    # Context-local suppression keeps financial HTTP/SQL data out of API auto-instrumentation.
    with suppress_instrumentation():
        logger.info(
            "event=worker_started initialize=%s strategy=%s market=KRW-BTC",
            initialize,
            settings.strategy,
        )
        engine = create_async_engine(
            settings.database_url, poolclass=NullPool, hide_parameters=True
        )
        try:
            async with open_store(engine, settings.identity, initialize=initialize) as store:
                if initialize:
                    print(
                        "최초 설치 승인 기록 완료. 계좌 검증·상태 생성은 다음 워커 기동에서 수행합니다."
                    )
                    return 0
                api = Upbit(settings)
                try:
                    trader = Trader(api, store, settings)
                    heartbeat = monotonic()
                    while True:
                        result = await trader.tick()
                        if on_cycle is not None:
                            on_cycle(result)
                        if monotonic() - heartbeat >= 60:
                            logger.info("event=worker_heartbeat result=%s", result)
                            heartbeat = monotonic()
                        if once:
                            return 0
                        await asyncio.sleep(settings.poll_seconds)
                finally:
                    await api.close()
        finally:
            await engine.dispose()
            logger.info("event=worker_stopped")


class TradingRuntime:
    def __init__(self) -> None:
        # Shared with Actuator info; never expose configuration, balances, or exception messages.
        self.info: dict[str, str | None] = {
            "status": "not-started",
            "reason": None,
            "last_result": None,
            "last_cycle_at": None,
            "error_type": None,
        }

    def record_cycle(self, result: str) -> None:
        self.info.update(
            status="halted" if result == "halted" else "running",
            last_result=result,
            last_cycle_at=datetime.now(UTC).isoformat(),
        )

    def _failed(self, error: Exception) -> None:
        self.info.update(status="failed", error_type=type(error).__name__)
        if isinstance(error, StateUnavailable):
            self.info["reason"] = error.reason
        logger.error(
            "event=worker_failed error_type=%s action=inspect_state_before_restart",
            type(error).__name__,
        )

    async def _run(self, settings: TradingSettings) -> None:
        try:
            await run(settings, initialize=False, once=False, on_cycle=self.record_cycle)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Consume background failures without leaking payloads or silently restarting orders.
            self._failed(error)
        else:
            self._failed(RuntimeError("Trading loop exited unexpectedly"))

    @asynccontextmanager
    async def lifespan(self, platform: PlatformSettings) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        try:
            if not platform.platform_integrations_enabled:
                self.info.update(status="disabled", reason="local_profile")
            else:
                settings = TradingSettings()
                if not settings.live_enabled:
                    self.info.update(status="disabled", reason="live_disabled")
                else:
                    settings.require_credentials()
                    self.info.update(status="starting")
                    task = asyncio.create_task(self._run(settings), name="evergreen-trading")
        except Exception as error:
            self._failed(error)
        if self.info["status"] == "disabled":
            logger.info("event=worker_skipped reason=%s", self.info["reason"])
        try:
            yield
        finally:
            if task is not None:
                if not task.done():
                    self.info.update(status="stopping")
                    logger.info("event=worker_stopping")
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    self.info.update(status="stopped")
