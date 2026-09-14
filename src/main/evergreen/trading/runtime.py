"""Trading loop owned by the API lifespan, with explicit failure and shutdown state."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic

from opentelemetry.instrumentation.utils import suppress_instrumentation
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.platform.config import PlatformSettings
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader
from evergreen.trading.recovery import Recovery, classify, delay
from evergreen.trading.state import StateUnavailable, open_store
from evergreen.trading.upbit import Upbit

logger = logging.getLogger(__name__)


async def run(
    settings: TradingSettings,
    *,
    initialize: bool,
    once: bool,
    on_cycle: Callable[[str], None] | None = None,
    on_recovery: Callable[[Recovery, int, float, str], None] | None = None,
) -> int:
    """Single recovery boundary shared by API and CLI; recreate all resources on retry."""
    attempt = 0
    recovering = False

    def completed(result: str) -> None:
        nonlocal attempt, recovering
        if recovering:
            logger.info("event=worker_recovered result=%s", result)
        attempt, recovering = 0, False
        if on_cycle is not None:
            on_cycle(result)

    while True:
        try:
            return await _session(settings, initialize=initialize, once=once, on_cycle=completed)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if once or initialize:
                raise
            policy = (
                Recovery(error.reason, 60)
                if isinstance(error, StateUnavailable)
                else classify(error)
            )
            attempt = min(attempt + 1, 1000000)
            wait = delay(attempt, policy)
            recovering = True
            if on_recovery is not None:
                on_recovery(policy, attempt, wait, type(error).__name__)
            logger.warning(
                "event=worker_recovering reason=%s error_type=%s attempt=%d retry_seconds=%s",
                policy.reason,
                type(error).__name__,
                attempt,
                wait,
            )
            await asyncio.sleep(wait)


async def _cleanup(close: Callable[[], Awaitable[None]], failed: bool) -> None:
    try:
        await close()
    except Exception:
        if not failed:
            raise
        logger.warning("event=worker_cleanup_failed action=preserve_original_failure")


async def _session(
    settings: TradingSettings,
    *,
    initialize: bool,
    once: bool,
    on_cycle: Callable[[str], None],
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
        failed = False
        try:
            async with open_store(engine, settings.identity, initialize=initialize) as store:
                if initialize:
                    print(
                        "최초 설치 승인 기록 완료. 계좌 검증·상태 생성은 다음 워커 기동에서 수행합니다."
                    )
                    return 0
                api = Upbit(settings)
                cycle_failed = False
                try:
                    trader = Trader(api, store, settings)
                    heartbeat = monotonic()
                    while True:
                        result = await trader.tick()
                        on_cycle(result)
                        if monotonic() - heartbeat >= 60:
                            logger.info("event=worker_heartbeat result=%s", result)
                            heartbeat = monotonic()
                        if once:
                            return 0
                        await asyncio.sleep(settings.poll_seconds)
                except BaseException:
                    cycle_failed = True
                    raise
                finally:
                    await _cleanup(api.close, cycle_failed)
        except BaseException:
            failed = True
            raise
        finally:
            await _cleanup(engine.dispose, failed)
            logger.info("event=worker_stopped")


class TradingRuntime:
    def __init__(self) -> None:
        # Shared with Actuator info; never expose configuration, balances, or exception messages.
        self.info: dict[str, str | int | float | None] = {
            "status": "not-started",
            "reason": None,
            "last_result": None,
            "last_cycle_at": None,
            "error_type": None,
            "attempt": 0,
            "retry_seconds": None,
        }

    def record_cycle(self, result: str) -> None:
        self.info.update(
            status="halted" if result == "halted" else "running",
            last_result=result,
            last_cycle_at=datetime.now(UTC).isoformat(),
            reason=None,
            error_type=None,
            attempt=0,
            retry_seconds=None,
        )

    def record_recovery(self, policy: Recovery, attempt: int, wait: float, error_type: str) -> None:
        self.info.update(
            status="recovering",
            reason=policy.reason,
            error_type=error_type,
            attempt=attempt,
            retry_seconds=wait,
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
            await run(
                settings,
                initialize=False,
                once=False,
                on_cycle=self.record_cycle,
                on_recovery=self.record_recovery,
            )
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
