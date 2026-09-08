import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from upbit import APITimeoutError, NotFoundError

from evergreen.market import Candle
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader, check_depth
from evergreen.trading.state import State, Store
from evergreen.trading.upbit import Book, Chance, Order, Upbit

NOW = datetime(2026, 9, 7, 10, 0, 30, tzinfo=UTC)


def settings() -> TradingSettings:
    return TradingSettings(
        live_enabled=True, access_key=SecretStr("account"), secret_key=SecretStr("s" * 64)
    )


class MemoryStore(Store):
    def __init__(self, identity: str) -> None:
        self.state = State(identity=identity)
        self.owned = True

    async def load(self) -> State:
        return self.state.model_copy(deep=True)

    async def save(self, state: State, event: str, detail: dict[str, object] | None = None) -> None:
        await self.assert_owner()
        self.state = state.model_copy(deep=True)

    async def assert_owner(self) -> None:
        if not self.owned:
            raise RuntimeError("lock lost")


class FakeUpbit(Upbit):
    def __init__(self) -> None:
        self.cash = Decimal(100000)
        self.btc = Decimal(0)
        self.price = Decimal(110)
        self.locked = Decimal(0)
        self.open_orders = False
        self.stale = False
        self.missing = False
        self.timeout = False
        self.not_found = False
        self.order_state = "wait"
        self.sent: list[dict[str, str]] = []
        self.latest: dict[str, str] | None = None
        self.before_cash = self.cash
        self.before_btc = self.btc
        self.zero_fill = False

    async def chance(self) -> Chance:
        return Chance.model_validate(
            {
                "bid_fee": ".0005",
                "ask_fee": ".0005",
                "market": {
                    "id": "KRW-BTC",
                    "state": "active",
                    "bid": {"currency": "KRW", "min_total": "5000"},
                    "ask": {"currency": "BTC", "min_total": "5000"},
                    "bid_types": ["price"],
                    "ask_types": ["market"],
                    "max_total": "1000000000",
                },
                "bid_account": {
                    "currency": "KRW",
                    "balance": str(self.cash),
                    "locked": str(self.locked),
                },
                "ask_account": {"currency": "BTC", "balance": str(self.btc), "locked": "0"},
            }
        )

    async def book(self) -> Book:
        return Book.model_validate(
            {
                "market": "KRW-BTC",
                "timestamp": int(NOW.timestamp() * 1000) - (60000 if self.stale else 0),
                "orderbook_units": [
                    {
                        "bid_price": str(self.price - 1),
                        "ask_price": str(self.price),
                        "bid_size": "10000",
                        "ask_size": "10000",
                    }
                ],
            }
        )

    async def has_open_orders(self) -> bool:
        return self.open_orders

    async def candles(self, end: datetime, now: datetime) -> list[Candle]:
        result = [
            Candle(
                open_time=end - timedelta(hours=169 - i),
                open=Decimal(100),
                high=Decimal(110 if i == 168 else 101),
                low=Decimal(99),
                close=Decimal(110 if i == 168 else 100),
                volume=Decimal(1),
                quote_volume=Decimal(100),
                fetched_at=now,
            )
            for i in range(169)
        ]
        return result[:-1] if self.missing else result

    async def submit(self, params: dict[str, str]) -> Order:
        self.sent.append(params.copy())
        self.latest = params.copy()
        self.before_cash, self.before_btc = self.cash, self.btc
        if self.timeout:
            raise APITimeoutError(request=httpx.Request("POST", "https://api.upbit.com/v1/orders"))
        return await self.order(params["identifier"])

    async def order(self, identifier: str) -> Order:
        if self.not_found:
            raise NotFoundError(
                "order_not_found",
                response=httpx.Response(
                    404, request=httpx.Request("GET", "https://api.upbit.com/v1/order")
                ),
                body=None,
            )
        assert self.latest is not None and identifier == self.latest["identifier"]
        volume = (
            abs(self.btc - self.before_btc)
            if self.order_state in ("done", "cancel")
            else Decimal(0)
        )
        if self.zero_fill:
            volume = Decimal(0)
        fee = Decimal(50) if volume else Decimal(0)
        funds = (
            self.before_cash - self.cash - fee
            if self.latest["side"] == "bid"
            else self.cash - self.before_cash + fee
        )
        return Order.model_validate(
            {
                "uuid": "uuid",
                "identifier": identifier,
                "market": "KRW-BTC",
                "side": self.latest["side"],
                "state": self.order_state,
                "executed_volume": str(volume),
                "paid_fee": str(fee),
                "trades": [{"funds": str(funds), "volume": str(volume)}] if volume else [],
            }
        )


@pytest.mark.parametrize("ambiguous", [False, True])
@pytest.mark.asyncio
async def test_intent_survives_restart_and_no_duplicate_post(
    ambiguous: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    api, config = FakeUpbit(), settings()
    api.timeout = ambiguous
    store = MemoryStore(config.identity)
    runner = Trader(api, store, config, lambda: NOW)
    if ambiguous:
        with pytest.raises(APITimeoutError):
            await runner.tick()
    else:
        assert await runner.tick() == "submitted"
    assert (await store.load()).pending == api.sent[0]
    assert Decimal(api.sent[0]["price"]) * Decimal("1.0005") <= api.cash
    runner = Trader(api, store, config, lambda: NOW)
    assert await runner.tick() == "pending"
    api.order_state, api.cash, api.btc = "done", Decimal(1), Decimal(900)
    assert await runner.tick() == "reconciled"
    assert await runner.tick() == "already-evaluated"
    assert len(api.sent) == 1
    assert (await store.load()).btc == 900 and (await store.load()).pending is None
    assert "event=order_intent_committed order_sequence=1" in caplog.text
    assert "event=order_reconciled order_sequence=1 state=done" in caplog.text
    assert api.sent[0]["identifier"] not in caplog.text
    assert config.identity not in caplog.text
    assert "event=trading_cycle_result result=already-evaluated" not in caplog.text
    if ambiguous:
        assert "event=order_submit status=failed error_type=APITimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_not_found_after_timeout_never_reposts(caplog: pytest.LogCaptureFixture) -> None:
    api, config = FakeUpbit(), settings()
    api.timeout = True
    store = MemoryStore(config.identity)
    runner = Trader(api, store, config, lambda: NOW)
    with pytest.raises(APITimeoutError):
        await runner.tick()
    api.not_found = True
    for _ in range(3):
        with pytest.raises(NotFoundError):
            await runner.tick()
    assert len(api.sent) == 1 and (await store.load()).pending is not None
    assert "event=order_reconcile_lookup status=failed error_type=NotFoundError" in caplog.text


@pytest.mark.asyncio
async def test_drawdown_sells_all_and_halt_persists(caplog: pytest.LogCaptureFixture) -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    runner = Trader(api, store, config, lambda: NOW)
    assert await runner.tick() == "submitted"
    api.order_state, api.cash, api.btc = "done", Decimal(1), Decimal(900)
    assert await runner.tick() == "reconciled"
    api.price = Decimal(95)
    assert await runner.tick() == "submitted"
    assert api.sent[-1]["side"] == "ask" and Decimal(api.sent[-1]["volume"]) == 900
    assert (await store.load()).halted
    api.cash, api.btc = Decimal(84000), Decimal(0)
    assert await runner.tick() == "reconciled"
    assert await Trader(api, store, config, lambda: NOW).tick() == "halted"
    assert len(api.sent) == 2
    assert "event=trading_halted reason=max_drawdown" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("stale", "stale_quote"),
        ("missing", "invalid_candles"),
        ("open_orders", "existing_open_orders"),
    ],
)
async def test_rejection_logs_distinct_safe_reason(
    field: str, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    api, config = FakeUpbit(), settings()
    setattr(api, field, True)
    with pytest.raises(ValueError):
        await Trader(api, MemoryStore(config.identity), config, lambda: NOW).tick()
    assert f"event=trading_rejected reason={reason}" in caplog.text
    assert not api.sent


@pytest.mark.asyncio
async def test_slow_submission_log_cannot_bypass_final_quote_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    current = NOW

    class SlowHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            nonlocal current
            if record.getMessage() == "event=order_submit status=started":
                current += timedelta(seconds=6)

    logger = logging.getLogger("evergreen.trading.engine")
    handler = SlowHandler()
    logger.addHandler(handler)
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    try:
        with pytest.raises(ValueError, match="만료"):
            await Trader(api, store, config, lambda: current).tick()
        assert not api.sent
        assert store.state.pending is not None
        assert "reason=expired_before_submission" in caplog.text
    finally:
        logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize("field", ["stale", "missing", "open_orders", "locked", "btc"])
@pytest.mark.asyncio
async def test_invalid_inputs_block_orders(field: str) -> None:
    api, config = FakeUpbit(), settings()
    setattr(api, field, Decimal(100) if field in ("btc", "locked") else True)
    store = MemoryStore(config.identity)
    with pytest.raises(ValueError):
        await Trader(api, store, config, lambda: NOW).tick()
    assert not api.sent and (await store.load()).pending is None


@pytest.mark.asyncio
async def test_manual_balance_change_does_not_reset_peak() -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    state = await store.load()
    state.krw, state.btc, state.peak = api.cash, api.btc, Decimal(120000)
    await store.save(state, "test-initial")
    api.cash += 100
    with pytest.raises(ValueError, match="잔고"):
        await Trader(api, store, config, lambda: NOW).tick()
    assert (await store.load()).peak == 120000 and (await store.load()).halted
    assert not api.sent


@pytest.mark.asyncio
async def test_depth_rejects_insufficient_liquidity_and_slippage() -> None:
    book = await FakeUpbit().book()
    with pytest.raises(ValueError, match="잔량"):
        check_depth(book, "bid", Decimal(999999999), Decimal(".003"))
    poor = Book.model_validate(
        {
            "market": "KRW-BTC",
            "timestamp": book.timestamp,
            "orderbook_units": [
                {"bid_price": "99", "ask_price": "100", "bid_size": "1", "ask_size": "1"},
                {"bid_price": "80", "ask_price": "120", "bid_size": "100", "ask_size": "100"},
            ],
        }
    )
    with pytest.raises(ValueError, match="슬리피지"):
        check_depth(poor, "bid", Decimal(1000), Decimal(".003"))


def test_default_cli_has_no_network_or_config_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.trading.__main__ import main

    def fail(*args: object) -> None:
        pytest.fail("default must not contact Config Server or Upbit")

    monkeypatch.setattr("evergreen.trading.__main__.load_spring_config", fail)
    assert main([]) == 0


@pytest.mark.asyncio
async def test_lost_lock_after_intent_blocks_post(monkeypatch: pytest.MonkeyPatch) -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    original = store.save

    async def save(state: State, event: str, detail: dict[str, object] | None = None) -> None:
        await original(state, event, detail)
        if event == "order-intent":
            store.owned = False

    monkeypatch.setattr(store, "save", save)
    with pytest.raises(RuntimeError, match="lock"):
        await Trader(api, store, config, lambda: NOW).tick()
    assert not api.sent and store.state.pending is not None


@pytest.mark.asyncio
async def test_partial_cancel_risk_exit_uses_new_identifier() -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    runner = Trader(api, store, config, lambda: NOW)
    assert await runner.tick() == "submitted"
    api.order_state, api.cash, api.btc = "done", Decimal(1), Decimal(900)
    assert await runner.tick() == "reconciled"
    api.price = Decimal(95)
    assert await runner.tick() == "submitted"
    first_exit = api.sent[-1]["identifier"]
    api.order_state, api.cash, api.btc = "cancel", Decimal(42000), Decimal(450)
    assert await runner.tick() == "reconciled"
    api.order_state = "wait"
    assert await runner.tick() == "submitted"
    assert api.sent[-1]["identifier"] != first_exit
    assert Decimal(api.sent[-1]["volume"]) == 450


@pytest.mark.asyncio
@pytest.mark.parametrize("external", ["deposit", "btc"])
async def test_external_balance_change_during_pending_does_not_increase_capital(
    external: str,
) -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    runner = Trader(api, store, config, lambda: NOW)
    assert await runner.tick() == "submitted"
    before = await store.load()
    api.order_state, api.zero_fill = "cancel", True
    if external == "deposit":
        api.cash += 100000
    else:
        api.btc += 100
    for _ in range(2):
        with pytest.raises(ValueError, match="예상 잔고"):
            await runner.tick()
        state = await store.load()
        assert state.halted and state.pending == before.pending
        assert (state.krw, state.btc, state.peak) == (before.krw, before.btc, before.peak)
    assert len(api.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [6, 600])
async def test_slow_intent_commit_blocks_stale_submission(
    delay: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore(config.identity)
    original = store.save
    current = NOW

    async def save(state: State, event: str, detail: dict[str, object] | None = None) -> None:
        nonlocal current
        await original(state, event, detail)
        if event == "order-intent":
            current += timedelta(seconds=delay)

    monkeypatch.setattr(store, "save", save)
    with pytest.raises(ValueError, match="만료"):
        await Trader(api, store, config, lambda: current).tick()
    assert not api.sent and store.state.pending is not None


def test_disabled_cli_redacts_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evergreen.trading.__main__ import main

    monkeypatch.setattr("evergreen.trading.__main__.load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr("evergreen.trading.__main__.load_spring_config", lambda *args: None)
    monkeypatch.setattr(
        "evergreen.trading.__main__.TradingSettings",
        lambda: settings().model_copy(update={"live_enabled": False}),
    )
    assert main(["--execute"]) == 1
    assert "ValueError" in capsys.readouterr().err


def test_cli_redacts_sdk_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evergreen.trading.__main__ import main

    async def fail(*args: object, **kwargs: object) -> int:
        raise NotFoundError(
            "private-account-data",
            response=httpx.Response(
                404, request=httpx.Request("GET", "https://api.upbit.com/v1/order")
            ),
            body={"private": "private-account-data"},
        )

    monkeypatch.setattr("evergreen.trading.__main__.load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr("evergreen.trading.__main__.load_spring_config", lambda *args: None)
    monkeypatch.setattr("evergreen.trading.__main__.TradingSettings", settings)
    monkeypatch.setattr("evergreen.trading.__main__.run", fail)
    assert main(["--execute"]) == 1
    error = capsys.readouterr().err
    assert "NotFoundError" in error
    assert "private-account-data" not in error
