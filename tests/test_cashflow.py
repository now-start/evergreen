import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy import create_engine, insert

from evergreen.trading.cashflow import Transfer, completed
from evergreen.trading.engine import Trader
from evergreen.trading.performance import Ledger
from evergreen.trading.state import State, Store, events, metadata
from evergreen.trading.upbit import Upbit
from test_trading_execution import NOW, FakeUpbit, MemoryStore, settings
from test_trading_performance import initial, snapshot


def transfer(kind: str = "withdraw", amount: str = "10000", fee: str = "1000") -> Transfer:
    return Transfer.model_validate(
        {
            "uuid": "private-transfer",
            "type": kind,
            "currency": "KRW",
            "state": "DONE" if kind == "withdraw" else "ACCEPTED",
            "amount": amount,
            "fee": fee,
            "created_at": NOW - timedelta(seconds=20),
            "done_at": NOW - timedelta(seconds=10),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,amount,fee", [("withdraw", "10000", "1000"), ("deposit", "5000", "0")]
)
@pytest.mark.parametrize("halted", [False, True])
async def test_verified_cashflow_preserves_risk_and_never_orders_during_reconciliation(
    kind: str,
    amount: str,
    fee: str,
    halted: bool,
) -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore()
    store.state.krw, store.state.btc = api.cash, api.btc
    store.state.peak = D(105000)
    store.state.halted = halted
    original = store.state.model_copy(deep=True)
    movement = transfer(kind, amount, fee)
    api.cash += movement.delta
    api.cash_movements = AsyncMock(return_value=[movement])  # type: ignore[method-assign]
    store.balance_baseline = AsyncMock(return_value=NOW - timedelta(hours=1))  # type: ignore[method-assign]
    trader = Trader(api, store, config, lambda: NOW)
    assert await trader.tick() == "cashflow-reconciled"
    assert not api.sent and store.state.krw == api.cash
    expected = (
        (D(100000) - movement.fee + movement.principal) * (D(105000) / (D(100000) - movement.fee))
        if kind == "withdraw"
        else (D(100000) + movement.principal) * D("1.05")
    )
    assert store.state.peak == expected
    assert store.state.halted == halted
    assert store.state.order_sequence == original.order_sequence
    assert store.state.buffer == original.buffer
    # A new worker must see the adopted balance, not apply the transfer twice.
    api.cash_movements.return_value = []
    await Trader(api, store, config, lambda: NOW).tick()
    assert api.cash_movements.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["unexplained", "btc", "changed", "no-history", "api-error", "late-transfer"]
)
async def test_unverified_changes_cannot_reset_state_or_submit(fault: str) -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore()
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, api.cash
    original = store.state.model_copy(deep=True)
    api.cash -= 11000
    movements = [transfer()]
    if fault == "unexplained":
        api.cash -= 1
    if fault == "btc":
        api.btc += D(".00001")
    if fault == "no-history":
        movements = []
    if fault == "late-transfer":
        api.cash = original.krw or D(0)
        movements = []

    async def read(*args: object) -> list[Transfer]:
        if fault == "api-error":
            raise RuntimeError("history unavailable")
        if fault in {"changed", "late-transfer"}:
            api.cash -= 1
        return movements

    api.cash_movements = AsyncMock(side_effect=read)  # type: ignore[method-assign]
    store.balance_baseline = AsyncMock(return_value=NOW - timedelta(hours=1))  # type: ignore[method-assign]
    with pytest.raises((ValueError, RuntimeError)):
        await Trader(api, store, config, lambda: NOW).tick()
    assert store.state == original and not api.sent


def test_history_boundaries_and_duplicates() -> None:
    row = transfer()
    assert completed([row], NOW - timedelta(hours=1), NOW) == [row]
    assert completed([row], NOW, NOW) == []
    for rows, since, until in [
        ([row, row], NOW - timedelta(hours=1), NOW),
        ([row], NOW - timedelta(seconds=15), NOW),
        ([row], NOW - timedelta(hours=1), NOW - timedelta(seconds=15)),
        ([row.model_copy(update={"state": "WAITING"})], NOW - timedelta(hours=1), NOW),
    ]:
        with pytest.raises(ValueError):
            completed(rows, since, until)


def test_cashflow_accounting_excludes_principal_and_includes_fees() -> None:
    ledger = Ledger()
    initial(ledger, capital="100000")
    row = transfer()
    ledger.consume(
        "cashflow-reconciled",
        json.dumps(
            {
                "state": {"identity": "test", "krw": "89000", "btc": "0", "order_sequence": 0},
                "detail": {
                    "before_krw": "100000",
                    "principal_krw": "-10000",
                    "fees_krw": "1000",
                    "movements": [row.model_dump(mode="json")],
                },
            }
        ),
        NOW.isoformat(),
    )
    result = snapshot(ledger)
    assert result["status"] == "complete"
    assert D(str(result["total_pnl_krw"])) == -1000
    assert D(str(result["total_return_pct"])) == -1
    assert ledger.capital == 100000 and ledger.realized == -1000


@pytest.mark.asyncio
async def test_sdk_cashflow_reads_both_histories_and_never_posts() -> None:
    seen = []

    def fetch(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.host == "api.upbit.com"
        assert request.url.params["currency"] == "KRW"
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json=[transfer().model_dump(mode="json")]
            if request.url.path == "/v1/withdraws"
            else [],
        )

    api = Upbit(settings(), transport=httpx.MockTransport(fetch))
    try:
        assert await api.cash_movements(NOW - timedelta(hours=1), NOW) == [transfer()]
        assert seen == ["/v1/deposits", "/v1/withdraws"]
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_full_withdrawal_and_refunding_preserve_drawdown() -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore()
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, D(105000)
    api.cash = D(0)
    api.cash_movements = AsyncMock(return_value=[transfer(amount="99000")])  # type: ignore[method-assign]
    store.balance_baseline = AsyncMock(return_value=NOW - timedelta(hours=1))  # type: ignore[method-assign]
    assert await Trader(api, store, config, lambda: NOW).tick() == "cashflow-reconciled"
    assert store.state.peak == 0 and not store.state.halted
    ratio = D(105000) / D(99000)
    assert store.state.empty_peak_ratio == ratio
    api.cash = D(50000)
    api.cash_movements = AsyncMock(return_value=[transfer("deposit", "50000", "0")])  # type: ignore[method-assign]
    assert await Trader(api, store, config, lambda: NOW).tick() == "cashflow-reconciled"
    assert store.state.peak == D(50000) * ratio and not store.state.halted


@pytest.mark.asyncio
async def test_offsetting_transfers_are_recorded_even_when_balance_is_unchanged() -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore()
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, api.cash
    deposit = transfer("deposit", "11000", "0").model_copy(update={"uuid": "deposit"})
    api.cash_movements = AsyncMock(return_value=[deposit, transfer()])  # type: ignore[method-assign]
    store.balance_baseline = AsyncMock(return_value=NOW - timedelta(hours=1))  # type: ignore[method-assign]
    assert await Trader(api, store, config, lambda: NOW).tick() == "cashflow-reconciled"
    assert store.state.krw == 100000 and store.state.peak > 100000
    assert not api.sent


@pytest.mark.asyncio
async def test_deposit_cannot_hide_existing_drawdown_breach() -> None:
    api, config = FakeUpbit(), settings()
    store = MemoryStore()
    store.state.krw, store.state.btc, store.state.peak = api.cash, api.btc, D(120000)
    api.cash += 50000
    api.cash_movements = AsyncMock(return_value=[transfer("deposit", "50000", "0")])  # type: ignore[method-assign]
    store.balance_baseline = AsyncMock(return_value=NOW - timedelta(hours=1))  # type: ignore[method-assign]
    assert await Trader(api, store, config, lambda: NOW).tick() == "cashflow-reconciled"
    assert store.state.halted and store.state.peak == 180000 and not api.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_history_paginates_by_uuid_and_rejects_repeated_page(duplicate: bool) -> None:
    old = transfer("deposit", "100", "0").model_dump(mode="json")
    old.update(
        created_at=(NOW - timedelta(days=2)).isoformat(),
        done_at=(NOW - timedelta(days=1)).isoformat(),
    )
    page = [{**old, "uuid": f"old-{index}"} for index in range(100)]
    calls = []

    def fetch(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/withdraws":
            return httpx.Response(200, json=[transfer().model_dump(mode="json")])
        if "to" not in request.url.params or duplicate:
            return httpx.Response(200, json=page)
        assert request.url.params["to"] == "old-99"
        return httpx.Response(200, json=[])

    api = Upbit(settings(), transport=httpx.MockTransport(fetch))
    try:
        if duplicate:
            with pytest.raises(ValueError):
                await api.cash_movements(NOW - timedelta(hours=1), NOW)
            assert len(calls) == 2
        else:
            assert await api.cash_movements(NOW - timedelta(hours=1), NOW) == [transfer()]
            assert len(calls) == 3
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_journal_recovery_skips_operational_retries_and_replays_cashflow() -> None:
    # Exercise the actual SQL/journal paths without a production DB. Only the
    # MariaDB connection ownership query is replaced; its tests use MariaDB.
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.connect() as sync:

        @asynccontextmanager
        async def transaction() -> AsyncIterator[None]:
            with sync.begin():
                yield

        connection = Mock()
        connection.begin = transaction
        connection.execute = AsyncMock(side_effect=sync.execute)
        store = Store(connection, 1)
        store._check_owner = AsyncMock()  # type: ignore[method-assign]
        await store.approve_initialization()
        state = State(krw=D(100000), btc=D(0))
        await store.initialize(state)
        baseline = await store.balance_baseline(state)
        assert baseline is not None
        with sync.begin():
            sync.execute(
                insert(events),
                [
                    {
                        "time": NOW.isoformat(),
                        "event": "unexpected-balance",
                        "payload": "{}",
                    }
                    for _ in range(10001)
                ],
            )
        assert await store.balance_baseline(state) == baseline
        assert (await store.performance()).reason is None
        state.krw = D(89000)
        await store.save(
            state,
            "cashflow-reconciled",
            {
                "before_krw": "100000",
                "principal_krw": "-10000",
                "fees_krw": "1000",
                "movements": [transfer().model_dump(mode="json")],
            },
        )
        assert await store.balance_baseline(state) != baseline
        ledger = await store.performance()
        assert ledger.cash == 89000 and ledger.net_flows == -10000 and ledger.reason is None
        restarted = Store(connection, 1)
        restarted._check_owner = AsyncMock()  # type: ignore[method-assign]
        assert await restarted.load() == state
        assert (await restarted.performance()).realized == -1000
        state.krw = D(1)
        assert await restarted.balance_baseline(state) is None
    engine.dispose()
