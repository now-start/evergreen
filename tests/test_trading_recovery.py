"""Deployment interruptions must resume durable cursors, never replay old buys."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest

from evergreen.market import Candle
from evergreen.strategies.buffer import ID, BufferState, Protection
from evergreen.trading.engine import Trader
from evergreen.trading.upbit import Upbit
from test_buffer_execution import CandidateUpbit, candidate
from test_trading_execution import NOW, MemoryStore

HOUR = NOW.replace(minute=0, second=0, microsecond=0)


def raw_bar(opening: datetime) -> dict[str, object]:
    return {
        "market": "KRW-BTC",
        "unit": 60,
        "candle_date_time_utc": opening.astimezone(UTC).isoformat(),
        "opening_price": 100,
        "high_price": 101,
        "low_price": 99,
        "trade_price": 100,
        "candle_acc_trade_volume": 1,
        "candle_acc_trade_price": 100,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [201, 400, 769])
async def test_sdk_pages_backwards_to_exact_recovery_start(hours: int) -> None:
    requests: list[httpx.Request] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        end = datetime.fromisoformat(request.url.params["to"])
        count = int(request.url.params["count"])
        assert request.method == "GET" and 1 <= count <= 200
        return httpx.Response(
            200, json=[raw_bar(end - timedelta(hours=i)) for i in range(1, count + 1)]
        )

    api = Upbit(candidate(), transport=httpx.MockTransport(fetch))
    try:
        start = HOUR - timedelta(hours=hours)
        bars = await api.candles(HOUR, NOW, start=start)
        assert [bar.open_time for bar in bars] == [start + timedelta(hours=i) for i in range(hours)]
        assert len(requests) == (hours + 199) // 200
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["empty", "missing", "duplicate", "repeat", "future", "timeout"])
async def test_bad_second_page_is_rejected_without_retry(fault: str) -> None:
    calls = 0

    def fetch(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        end = datetime.fromisoformat(request.url.params["to"])
        count = int(request.url.params["count"])
        data = [raw_bar(end - timedelta(hours=i)) for i in range(1, count + 1)]
        if calls == 2:
            if fault == "timeout":
                raise httpx.ReadTimeout("test", request=request)
            if fault == "empty":
                data = []
            elif fault == "missing":
                data.pop()
            elif fault == "duplicate":
                data[-1] = data[0]
            elif fault in {"repeat", "future"}:
                data[0] = raw_bar(HOUR if fault == "future" else HOUR - timedelta(hours=1))
        return httpx.Response(200, json=data)

    from upbit import APITimeoutError

    api = Upbit(candidate(), transport=httpx.MockTransport(fetch))
    try:
        with pytest.raises((ValueError, APITimeoutError)):
            await api.candles(HOUR, NOW, start=HOUR - timedelta(hours=400))
        assert calls == 2
    finally:
        await api.close()


class RecoveryUpbit(CandidateUpbit):
    def __init__(self) -> None:
        super().__init__()
        self.windows: list[datetime] = []
        self.fail = False
        self.delay = False
        self.old_buy = False

    async def candles(
        self, end: datetime, now: datetime, *, start: datetime | None = None
    ) -> list[Candle]:
        start = start or end - timedelta(hours=200)
        self.windows.append(start)
        if self.fail:
            raise asyncio.CancelledError
        bars = [
            Candle(
                open_time=start + timedelta(hours=i),
                open=D(100),
                high=D(101),
                low=D(99),
                close=D(100),
                volume=D(1),
                quote_volume=D(100),
                fetched_at=now,
            )
            for i in range(int((end - start).total_seconds() // 3600))
        ]
        index = -40 if self.old_buy else -1
        bars[index] = bars[index].model_copy(update={"high": D(110), "close": D(110)})
        if self.delay:
            self.now += timedelta(minutes=5)
        return bars


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [1, 2, 32, 33, 72, 601])
async def test_restart_recovers_every_bar_once_outside_buy_window(
    gap: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen.trading.chart")
    cfg, api = candidate(), RecoveryUpbit()
    api.now += timedelta(minutes=10)
    store = MemoryStore(cfg.identity)
    cursor = HOUR - timedelta(hours=gap)
    store.state.strategy, store.state.buffer = ID, BufferState(last_bar=cursor)
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, api.cash
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "no-signal"
    assert store.state.buffer is not None and store.state.buffer.last_bar == HOUR
    assert api.windows == [min(HOUR - timedelta(hours=200), cursor - timedelta(hours=168))]
    assert not api.sent
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "already-evaluated"
    assert len(api.windows) == 1
    snapshots = [
        snapshot
        for record in caplog.records
        if record.name == "evergreen.trading.chart"
        and (snapshot := json.loads(record.getMessage()))["event"] == "strategy_bar"
    ]
    assert len(snapshots) == gap
    assert len({bar["event_id"] for bar in snapshots}) == gap
    assert snapshots[-1]["pre_order_signal"] is None


@pytest.mark.asyncio
async def test_cancellation_during_second_sdk_page_returns_no_partial_history() -> None:
    reached = asyncio.Event()
    calls = 0

    async def fetch(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            reached.set()
            await asyncio.Event().wait()
        end = datetime.fromisoformat(request.url.params["to"])
        return httpx.Response(200, json=[raw_bar(end - timedelta(hours=i)) for i in range(1, 201)])

    api = Upbit(candidate(), transport=httpx.MockTransport(fetch))
    task = asyncio.create_task(api.candles(HOUR, NOW, start=HOUR - timedelta(hours=400)))
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await api.close()


@pytest.mark.asyncio
async def test_cancelled_catchup_keeps_cursor_and_restart_recovers() -> None:
    cfg, api = candidate(), RecoveryUpbit()
    api.now += timedelta(minutes=10)
    store = MemoryStore(cfg.identity)
    cursor = HOUR - timedelta(hours=72)
    store.state.strategy, store.state.buffer = ID, BufferState(last_bar=cursor)
    store.state.krw, store.state.btc = api.cash, api.btc
    api.fail = True
    with pytest.raises(asyncio.CancelledError):
        await Trader(api, store, cfg, lambda: api.now).tick()
    assert store.state.buffer is not None and store.state.buffer.last_bar == cursor
    assert store.state.pending is None and not api.sent
    api.fail = False
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "no-signal"
    assert store.state.buffer is not None and store.state.buffer.last_bar == HOUR


@pytest.mark.asyncio
@pytest.mark.parametrize("old_buy,delay", [(True, False), (False, True)])
async def test_historical_or_expired_buy_never_submits(old_buy: bool, delay: bool) -> None:
    cfg, api = candidate(), RecoveryUpbit()
    api.old_buy, api.delay = old_buy, delay
    store = MemoryStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState(last_bar=HOUR - timedelta(hours=72))
    store.state.krw, store.state.btc = api.cash, api.btc
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "no-signal"
    assert store.state.buffer is not None and store.state.buffer.last_bar == HOUR
    assert store.state.pending is None and not api.sent


@pytest.mark.asyncio
async def test_held_position_recovers_exit_after_long_downtime() -> None:
    cfg, api = candidate(), RecoveryUpbit()
    api.now += timedelta(minutes=10)
    api.cash, api.btc = D(0), D(1000)
    store = MemoryStore(cfg.identity)
    cursor = HOUR - timedelta(hours=72)
    store.state.strategy = ID
    store.state.buffer = BufferState(
        last_bar=cursor,
        position_since=cursor - timedelta(hours=1),
        protection=Protection.open(D(120), D(2)),
    )
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, D(110000)
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    assert len(api.sent) == 1 and api.sent[0]["side"] == "ask"
    assert store.state.buffer is not None and store.state.buffer.last_bar == HOUR
    assert store.state.reserved_exit is not None
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "pending"
    assert len(api.sent) == 1
