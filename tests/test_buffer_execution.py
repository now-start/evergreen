from datetime import timedelta
from decimal import Decimal as D

import pytest

from evergreen.strategies.buffer import ID, BufferState, Protection
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader
from test_trading_execution import NOW, FakeUpbit, MemoryStore, settings


def candidate():
    data = {
        "live_enabled": True,
        "access_key": settings().access_key,
        "secret_key": settings().secret_key,
        "strategy": ID,
        "max_drawdown": D(".20"),
    }
    return TradingSettings.model_validate(data)


class CandidateUpbit(FakeUpbit):
    def __init__(self):
        super().__init__()
        self.now = NOW

    async def book(self):
        book = await super().book()
        return book.model_copy(update={"timestamp": int(self.now.timestamp() * 1000)})

    async def order(self, identifier):
        order = await super().order(identifier)
        return order.model_copy(
            update={"trades": [f.model_copy(update={"created_at": self.now}) for f in order.trades]}
        )

    async def candles(self, end, now):
        rows = await super().candles(end, now)
        return [
            rows[0].model_copy(update={"open_time": rows[0].open_time - timedelta(hours=i)})
            for i in range(31, 0, -1)
        ] + rows


def test_strategy_and_risk_contract():
    assert candidate().strategy == ID
    with pytest.raises(ValueError):
        TradingSettings(strategy="breakout-v1", max_drawdown=D(".20"))
    with pytest.raises(ValueError):
        TradingSettings(strategy=ID, max_drawdown=D(".10"))


@pytest.mark.asyncio
async def test_cash_transition_preserves_peak_and_does_not_order():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    store.state.krw, store.state.btc, store.state.peak = D(100000), D(0), D(101000)
    api = CandidateUpbit()
    assert await Trader(api, store, cfg, lambda: NOW).tick() == "strategy-transitioned"
    assert store.state.strategy == ID and store.state.buffer is not None
    assert store.state.peak == 101000 and not api.sent


@pytest.mark.asyncio
async def test_legacy_position_is_not_adopted_by_candidate():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    api = CandidateUpbit()
    api.cash, api.btc = D(0), D(1000)
    store.state.krw, store.state.btc = api.cash, api.btc
    assert await Trader(api, store, cfg, lambda: NOW).tick() == "no-signal"
    assert store.state.strategy == "breakout-v1" and not api.sent


@pytest.mark.asyncio
async def test_halted_account_is_not_reset_during_promotion():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    store.state.krw, store.state.btc, store.state.halted = D(100000), D(0), True
    assert await Trader(CandidateUpbit(), store, cfg, lambda: NOW).tick() == "halted"
    assert store.state.strategy == "breakout-v1" and store.state.halted


@pytest.mark.asyncio
async def test_candidate_buy_persists_signal_range_before_order():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    api = CandidateUpbit()
    assert await Trader(api, store, cfg, lambda: NOW).tick() == "submitted"
    assert store.state.intent_context.entry_tr == 2
    assert "entry_tr" not in api.sent[0]
    assert len(api.sent) == 1


@pytest.mark.asyncio
async def test_corrupt_candidate_position_blocks_new_orders():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    api = CandidateUpbit()
    api.cash, api.btc = D(0), D(1000)
    store.state.krw, store.state.btc = api.cash, api.btc
    with pytest.raises(ValueError):
        await Trader(api, store, cfg, lambda: NOW).tick()
    assert not api.sent


@pytest.mark.asyncio
async def test_ambiguous_buy_recovers_once_and_restores_protection():
    from upbit import APITimeoutError

    cfg = candidate()
    store = MemoryStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    api = CandidateUpbit()
    api.timeout = True
    with pytest.raises(APITimeoutError):
        await Trader(api, store, cfg, lambda: api.now).tick()
    assert store.state.pending and store.state.intent_context.entry_tr == 2
    api.timeout = False
    # New Trader instance simulates a process restart with the same durable state.
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "pending"
    api.cash, api.btc, api.order_state = D(0), D(1000), "done"
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "reconciled"
    assert store.state.buffer.protection.reference == D("99.95")
    assert store.state.buffer.protection.entry_tr == 2
    assert store.state.pending is None and store.state.intent_context is None
    assert len(api.sent) == 1
    api.now += timedelta(hours=1)
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "no-signal"
    assert store.state.buffer.last_bar == api.now.replace(minute=0, second=0, microsecond=0)


def held():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    api = CandidateUpbit()
    api.cash, api.btc = D(0), D(1000)
    store.state.strategy = ID
    protection = Protection.open(D(130), D(2))
    protection.breaches = 1
    store.state.buffer = BufferState(
        protection=protection,
        last_bar=NOW.replace(minute=0, second=0) - timedelta(hours=1),
        position_since=NOW - timedelta(hours=2),
    )
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, D(130000)
    original = api.candles

    async def candles(end, now):
        bars = await original(end, now)
        bars[-1] = bars[-1].model_copy(update={"open": D(110)})
        return bars

    api.candles = candles
    return cfg, store, api


@pytest.mark.asyncio
async def test_terminal_partial_sell_retains_exit_then_clears_position():
    cfg, store, api = held()
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    assert store.state.intent_context.reset_after_sell
    api.cash, api.btc, api.order_state = D(54950), D(500), "cancel"
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "reconciled"
    assert store.state.buffer.protection is not None and store.state.reserved_exit
    api.order_state = "wait"
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    assert len(api.sent) == 2 and api.sent[0]["identifier"] != api.sent[1]["identifier"]
    api.cash, api.btc, api.order_state = D(109900), D(0), "done"
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "reconciled"
    assert store.state.buffer.protection is None and store.state.buffer.awaiting_reset
    assert store.state.reserved_exit is None


@pytest.mark.asyncio
async def test_zero_fill_buy_does_not_create_a_position():
    cfg = candidate()
    store = MemoryStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    api = CandidateUpbit()
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    api.zero_fill, api.order_state = True, "cancel"
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "reconciled"
    assert store.state.buffer.protection is None and store.state.pending is None
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "already-evaluated"
    assert len(api.sent) == 1


@pytest.mark.asyncio
async def test_missing_history_blocks_recovery_instead_of_resetting_position():
    cfg, store, api = held()
    store.state.buffer.last_bar -= timedelta(hours=40)
    with pytest.raises(ValueError):
        await Trader(api, store, cfg, lambda: api.now).tick()
    assert not api.sent and store.state.buffer.protection is not None


@pytest.mark.asyncio
async def test_depth_rejection_keeps_durable_exit_reservation(monkeypatch):
    import evergreen.trading.engine as module

    cfg, store, api = held()

    def reject(*args):
        raise ValueError("depth")

    monkeypatch.setattr(module, "check_depth", reject)
    with pytest.raises(ValueError, match="depth"):
        await Trader(api, store, cfg, lambda: api.now).tick()
    assert store.state.reserved_exit is not None and store.state.pending is None
    assert not api.sent


def test_legacy_payload_loads_without_changing_identity_or_peak():
    from evergreen.trading.state import State

    old = State.model_validate_json('{"identity":"old","peak":"120000","halted":true}')
    assert old.strategy == "breakout-v1" and old.buffer is None
    assert old.identity == "old" and old.peak == 120000 and old.halted


@pytest.mark.asyncio
async def test_recovered_bar_drawdown_permanently_halts_even_if_quote_recovers():
    cfg, store, api = held()
    store.state.peak = D(110000)
    original = api.candles

    async def candles(end, now):
        bars = await original(end, now)
        bars[-1] = bars[-1].model_copy(update={"open": D(150), "high": D(150)})
        return bars

    api.candles = candles
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    assert store.state.halted and store.state.peak == 150000
    assert store.state.intent_context.reason == "risk"


@pytest.mark.asyncio
async def test_pre_fill_open_does_not_become_owned_equity_peak():
    cfg, store, api = held()
    store.state.peak = D(110000)
    store.state.buffer.protection = Protection.open(D(110), D(2))
    store.state.buffer.position_since = NOW - timedelta(hours=1)
    original = api.candles

    async def candles(end, now):
        bars = await original(end, now)
        bars[-1] = bars[-1].model_copy(update={"open": D(150), "high": D(150)})
        return bars

    api.candles = candles
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "no-signal"
    assert not store.state.halted and store.state.peak == 110000 and not api.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_risk_fill_second_precision_is_not_rejected(recovered):
    cfg, store, api = held()
    api.now = NOW.replace(microsecond=123456)
    if recovered:
        original = api.candles

        async def candles(end, now):
            bars = await original(end, now)
            bars[-1] = bars[-1].model_copy(update={"open": D(150), "high": D(150)})
            return bars

        api.candles = candles
    else:
        store.state.halted = True
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    assert store.state.intent_context.reason == "risk"
    api.cash, api.btc, api.order_state = D(109950), D(0), "done"
    original_order = api.order

    async def order(identifier):
        result = await original_order(identifier)
        return result.model_copy(
            update={
                "trades": [
                    f.model_copy(update={"created_at": api.now.replace(microsecond=0)})
                    for f in result.trades
                ]
            }
        )

    api.order = order
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "reconciled"
    assert store.state.pending is None and store.state.halted


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [None, NOW + timedelta(hours=1), NOW - timedelta(hours=1)])
async def test_invalid_fill_time_preserves_pending_intent(timestamp):
    cfg, store, api = held()
    assert await Trader(api, store, cfg, lambda: api.now).tick() == "submitted"
    api.cash, api.btc, api.order_state = D(109950), D(0), "done"
    original = api.order

    async def order(identifier):
        result = await original(identifier)
        return result.model_copy(
            update={
                "trades": [f.model_copy(update={"created_at": timestamp}) for f in result.trades]
            }
        )

    api.order = order
    with pytest.raises(ValueError):
        await Trader(api, store, cfg, lambda: api.now).tick()
    assert store.state.pending and store.state.buffer.protection is not None
    assert store.state.btc == 1000 and len(api.sent) == 1
