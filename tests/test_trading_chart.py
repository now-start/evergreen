import json
import logging
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from evergreen.strategies.buffer import ID, BufferState
from evergreen.trading.engine import Trader
from evergreen.trading.state import State
from test_buffer_execution import CandidateUpbit, candidate, held
from test_trading_execution import NOW, MemoryStore


class RecordingStore(MemoryStore):
    def __init__(self, identity: str) -> None:
        super().__init__(identity)
        self.history: list[tuple[str, dict[str, object] | None]] = []
        self.fail_event: str | None = None

    async def save(self, state: State, event: str, detail: dict[str, object] | None = None) -> None:
        if event == self.fail_event:
            raise RuntimeError("commit failed")
        await super().save(state, event, detail)
        self.history.append((event, detail))


def logs(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, object]]:
    return [
        data
        for record in caplog.records
        if record.name == "evergreen.trading.chart"
        and (data := json.loads(record.getMessage()))["event"] == event
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_performance_logged_only_after_valuation_commit(
    caplog: pytest.LogCaptureFixture,
    commit_fails: bool,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    cfg, api = candidate(), CandidateUpbit()
    store = RecordingStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    if commit_fails:
        store.fail_event = "valuation"
        with pytest.raises(RuntimeError, match="commit failed"):
            await Trader(api, store, cfg, lambda: api.now).tick()
        assert not logs(caplog, "account_performance") and not api.sent
    else:
        await Trader(api, store, cfg, lambda: api.now).tick()
        snapshots = logs(caplog, "account_performance")
        assert len(snapshots) == 1
        assert snapshots[0]["status"] == "unavailable"  # No journal in this fake store.
        assert snapshots[0]["realized_pnl_krw"] is None
        details = [detail for event, detail in store.history if event == "valuation"]
        assert details == [{"chart": snapshots}]


@pytest.mark.asyncio
async def test_snapshot_persisted_and_logged_once_without_changing_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    cfg, api = candidate(), CandidateUpbit()
    store = RecordingStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    trader = Trader(api, store, cfg, lambda: api.now)
    assert await trader.tick() == "submitted"
    snapshots = logs(caplog, "strategy_bar")
    assert len(snapshots) == 1
    bar = snapshots[0]
    assert bar["close"] == "110" and bar["entry_threshold"] == "101.101"
    assert bar["initial_stop"] is None and bar["trailing_stop"] is None
    assert bar["candidate_signal"] == "buy" and bar["position"] == "cash"
    assert bar["event_time"] == NOW.replace(second=0).timestamp()
    assert bar["observed_at"] == NOW.isoformat()
    details = [detail for name, detail in store.history if name == "strategy-evaluated"]
    assert details[0] is not None and details[0]["chart"] == snapshots
    assert await trader.tick() == "pending"
    assert len(logs(caplog, "strategy_bar")) == 1 and len(api.sent) == 1
    assert not logs(caplog, "order_execution")
    assert cfg.identity not in caplog.text and api.sent[0]["identifier"] not in caplog.text


@pytest.mark.asyncio
async def test_no_signal_catchup_records_each_bar_once(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    cfg, original_store, api = held(monkeypatch)
    store = RecordingStore(cfg.identity)
    store.state = original_store.state
    assert store.state.buffer is not None
    store.state.peak = D(110000)
    store.state.buffer.bought(D(100), D(2))
    store.state.buffer.position_since = NOW - timedelta(hours=4)
    store.state.buffer.last_bar = NOW.replace(second=0) - timedelta(hours=3)
    trader = Trader(api, store, cfg, lambda: api.now)
    assert await trader.tick() == "no-signal"
    snapshots = logs(caplog, "strategy_bar")
    assert len(snapshots) == 3
    assert len({bar["event_id"] for bar in snapshots}) == 3
    assert [bar["event_time"] for bar in snapshots] == [
        (NOW.replace(second=0) - timedelta(hours=i)).timestamp() for i in (2, 1, 0)
    ]
    assert snapshots[0]["initial_stop"] == "88"
    assert snapshots[0]["trailing_stop"] is None
    assert snapshots[-1]["trailing_active"] is True
    assert snapshots[-1]["trailing_stop"] == "98"
    assert all(bar["candidate_signal"] is None for bar in snapshots)
    assert await trader.tick() == "already-evaluated"
    assert len(logs(caplog, "strategy_bar")) == 3 and not api.sent


@pytest.mark.asyncio
async def test_snapshot_commit_failure_does_not_emit_or_submit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    cfg, api = candidate(), CandidateUpbit()
    store = RecordingStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    store.fail_event = "strategy-evaluated"
    with pytest.raises(RuntimeError, match="commit failed"):
        await Trader(api, store, cfg, lambda: NOW).tick()
    assert not logs(caplog, "strategy_bar") and not api.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("zero_fill", [False, True])
async def test_reconciled_execution_is_truthful_and_not_duplicated(
    zero_fill: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    cfg, api = candidate(), CandidateUpbit()
    store = RecordingStore(cfg.identity)
    store.state.strategy, store.state.buffer = ID, BufferState()
    store.state.krw, store.state.btc = D(100000), D(0)
    trader = Trader(api, store, cfg, lambda: api.now)
    assert await trader.tick() == "submitted"
    api.order_state = "cancel" if zero_fill else "done"
    api.zero_fill = zero_fill
    if not zero_fill:
        api.cash, api.btc = D(0), D(1000)
    assert await trader.tick() == "reconciled"
    executions = logs(caplog, "order_execution")
    assert len(executions) == (0 if zero_fill else 1)
    if executions:
        fill = executions[0]
        assert fill["fill_price"] == "99.95" and fill["quantity"] == "1000"
        assert fill["fee_amount"] == "50" and fill["fee_currency"] == "KRW"
        assert fill["event_time"] == NOW.timestamp()
        assert fill["aggregation"] == "terminal_order_vwap"
        assert fill["signal_time"] == NOW.replace(second=0).isoformat()
        details = [detail for name, detail in store.history if name == "order-terminal"]
        assert details[0] is not None and details[0]["chart"] == executions
    assert await trader.tick() == "already-evaluated"
    assert len(logs(caplog, "order_execution")) == len(executions)


@pytest.mark.asyncio
async def test_partial_sell_and_failed_commit_do_not_create_false_markers(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="evergreen")
    cfg, original_store, api = held(monkeypatch)
    store = RecordingStore(cfg.identity)
    store.state = original_store.state
    trader = Trader(api, store, cfg, lambda: api.now)
    assert await trader.tick() == "submitted"
    api.cash, api.btc, api.order_state = D(54950), D(500), "cancel"
    store.fail_event = "order-terminal"
    with pytest.raises(RuntimeError, match="commit failed"):
        await trader.tick()
    assert not logs(caplog, "order_execution") and store.state.pending
    store.fail_event = None
    assert await trader.tick() == "reconciled"
    fill = logs(caplog, "order_execution")[0]
    assert fill["quantity"] == "500" and fill["remaining_btc"] == "500"
    assert fill["terminal_state"] == "cancel" and fill["side"] == "sell"
    assert store.state.reserved_exit and store.state.buffer and store.state.buffer.exit_due


@pytest.mark.asyncio
async def test_capture_does_not_change_strategy_state_or_result() -> None:
    from evergreen.trading.buffer_execution import signal

    api, cfg = CandidateUpbit(), candidate()
    history = await api.candles(NOW.replace(second=0), NOW)
    original = State(
        identity=cfg.identity,
        strategy=ID,
        buffer=BufferState(awaiting_reset=True),
        krw=D(100000),
        btc=D(0),
        peak=D(100000),
    )
    observed = original.model_copy(deep=True)
    chance = await api.chance()
    snapshots: list[dict[str, object]] = []
    args = (history, chance, D(".001"))
    assert signal(original, *args, prior_peak=original.peak) == signal(
        observed,
        *args,
        prior_peak=observed.peak,
        snapshots=snapshots,
    )
    assert original == observed and len(snapshots) == 1


def test_missing_legacy_fill_time_is_not_replaced_with_observation_time() -> None:
    from evergreen.trading.chart import execution_snapshot
    from evergreen.trading.upbit import Order

    order = Order.model_validate(
        {
            "uuid": "private",
            "identifier": "private",
            "market": "KRW-BTC",
            "side": "bid",
            "state": "done",
            "executed_volume": "1",
            "paid_fee": ".05",
            "trades": [{"funds": "100", "volume": "1"}],
        }
    )
    record = execution_snapshot(State(identity="private", btc=D(1)), order, NOW)[0]
    assert record["event_time"] is None and record["filled_at"] is None
    assert record["fill_price"] == "100" and "private" not in json.dumps(record)


def test_json_body_survives_otel_logging_handler() -> None:
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor

    from evergreen.trading.chart import emit

    exporter = InMemoryLogRecordExporter()  # type: ignore[no-untyped-call]
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = LoggingHandler(logger_provider=provider)
    logger = logging.getLogger("evergreen.trading.chart")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    record: dict[str, object] = {"event": "strategy_bar", "close": "123.4", "initial_stop": None}
    try:
        emit([record])
        exported = exporter.get_finished_logs()
        assert len(exported) == 1
        assert json.loads(str(exported[0].log_record.body)) == record
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()
        provider.shutdown()


def test_dashboard_embeds_exact_readonly_sql_and_does_not_connect_fill_points() -> None:
    directory = Path(__file__).resolve().parents[1] / "docs" / "grafana"
    dashboard = json.loads((directory / "early3-dashboard.json").read_text())
    assert dashboard["timezone"] == "Asia/Seoul"
    panels = dashboard["panels"]
    for target, filename in zip(
        [*panels[0]["targets"], panels[1]["targets"][0], panels[2]["targets"][0]],
        ["prices.sql", "executions.sql", "decisions.sql", "orders.sql"],
        strict=True,
    ):
        query = target["rawSql"]
        assert query == (directory / filename).read_text()
        assert query.startswith("WITH evidence AS")
        assert "$.event_time" in query and "latest = 1" in query
        assert "${DS_MARIADB}" == target["datasource"]["uid"]
    style = panels[0]["fieldConfig"]
    assert style["defaults"]["custom"]["spanNulls"] is False
    assert {"id": "custom.lineWidth", "value": 0} in style["overrides"][0]["properties"]
