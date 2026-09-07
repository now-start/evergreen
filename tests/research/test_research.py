from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from evergreen.market import Candle, collect, validate_candles
from evergreen.research.backtest import Costs, run_backtest
from evergreen.strategies import sma_target

D = Decimal
START = datetime(2025, 1, 1, tzinfo=UTC)


def candle(index: int, price: str = "100", close: str | None = None) -> Candle:
    opening, closing = D(price), D(close or price)
    return Candle(
        open_time=START + timedelta(hours=index),
        open=opening,
        high=max(opening, closing),
        low=min(opening, closing),
        close=closing,
        volume=D("1"),
        quote_volume=D("100"),
        fetched_at=START + timedelta(days=100),
    )


def costs(**overrides: Decimal) -> Costs:
    return Costs.model_validate(
        {
            "buy_fee": "0",
            "sell_fee": "0",
            "buy_slippage": "0",
            "sell_slippage": "0",
            "min_notional": "1",
            "quantity_step": "0.00000001",
            **overrides,
        }
    )


def test_validation_sorts_deduplicates_without_collection_metadata() -> None:
    first = candle(0)
    duplicate = first.model_copy(update={"fetched_at": first.fetched_at + timedelta(hours=1)})
    result, report = validate_candles(
        [candle(1), first, duplicate], START, START + timedelta(hours=2)
    )
    assert result == [first, candle(1)]
    assert report.duplicates == 1
    assert report.valid


def test_validation_reports_gaps_conflicts_and_incomplete_candles() -> None:
    _, report = validate_candles(
        [candle(0), candle(0, "101"), candle(2)],
        START,
        START + timedelta(hours=3),
        as_of=START + timedelta(hours=2),
    )
    assert report.conflicts == [START]
    assert report.missing == [START + timedelta(hours=1), START + timedelta(hours=2)]
    assert report.incomplete == 1
    assert not report.valid


@pytest.mark.parametrize(
    "updates",
    [
        {"open": "NaN"},
        {"volume": "-1"},
        {"high": "99"},
        {"open_time": START.replace(tzinfo=None)},
        {"open_time": START + timedelta(minutes=1)},
    ],
)
def test_bad_candle_rejected(updates: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Candle.model_validate(candle(0).model_dump() | updates)


def raw(index: int) -> dict[str, object]:
    return {
        "market": "KRW-BTC",
        "unit": 60,
        "candle_date_time_utc": (START + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M:%S"),
        "opening_price": 100,
        "high_price": 100,
        "low_price": 100,
        "trade_price": 100,
        "candle_acc_trade_volume": 1,
        "candle_acc_trade_price": 100,
    }


def test_collector_paginates_and_preserves_raw(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.upbit.com"
        return httpx.Response(200, json=[raw(2), raw(1)] if len(requests) == 1 else [raw(0)])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        output = tmp_path / "dataset"
        result = collect(client, START, START + timedelta(hours=3), output, sleep=lambda _: None)
    assert len(result) == 3
    assert len(requests) == 2
    assert requests[1].url.params["to"].startswith("2025-01-01T01:00:00")
    assert (output / "raw/000001.json").exists()
    assert (output / "quality.json").exists()
    assert (output / "candles.jsonl").exists()
    with pytest.raises(FileExistsError):
        output.mkdir()


def test_collector_refuses_cursor_stall_and_keeps_failure(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[raw(1)]))
    ) as client:
        with pytest.raises(ValueError, match="cursor"):
            collect(
                client, START, START + timedelta(hours=2), tmp_path / "bad", sleep=lambda _: None
            )
    assert "failed" in (tmp_path / "bad/quality.json").read_text()
    assert not (tmp_path / "bad/candles.jsonl").exists()


def test_sma_warmup_and_hysteresis() -> None:
    assert sma_target([D(100)] * 59, False) is None
    assert sma_target([D(100)] * 60, False) is None
    assert sma_target([D(100)] * 60, True) is None
    assert sma_target([D(100)] * 40 + [D(110)] * 20, False) == "buy"
    assert sma_target([D(100)] * 40 + [D(90)] * 20, True) == "sell"


def test_all_in_round_trip_fees_slippage_and_settlement() -> None:
    bars = [candle(i) for i in range(60)] + [candle(60), candle(61)]
    result = run_backtest(
        bars,
        START + timedelta(hours=60),
        D("1000"),
        costs(
            buy_fee=D("0.01"), sell_fee=D("0.02"), buy_slippage=D("0.01"), sell_slippage=D("0.01")
        ),
        "buy-hold",
    )
    buy, sell = result.fills
    assert buy.price == D("101")
    assert buy.quantity * buy.price + buy.fee <= D("1000")
    assert sell.price == D("99")
    assert sell.reason == "settlement"
    assert result.natural_exits == 0
    assert result.final_equity == result.cash + result.btc * D(100)
    assert result.total_fees == buy.fee + sell.fee
    assert result.net_return < 0


def test_signal_uses_next_open_not_signal_close() -> None:
    bars = [candle(i, "100" if i < 40 else "110") for i in range(60)]
    bars += [candle(60, "120"), candle(61, "125")]
    result = run_backtest(bars, START + timedelta(hours=60), D(1000), costs(), "sma-trend-v0")
    assert result.fills[0].price == D(120)
    assert result.fills[0].signal_time == bars[59].close_time
    assert result.fills[0].time == bars[60].open_time
    assert len([f for f in result.fills if f.side == "buy"]) == 1


def test_drawdown_uses_high_water_and_exits_after_gap_without_reentry() -> None:
    bars = [candle(i, "100" if i < 40 else "110") for i in range(60)]
    bars += [
        candle(60, "110", "200"),
        candle(61, "200", "175"),
        candle(62, "150"),
        candle(63, "300"),
    ]
    result = run_backtest(bars, START + timedelta(hours=60), D(1100), costs(), "sma-trend-v0")
    assert result.halted
    assert result.fills[1].reason == "risk"
    assert result.fills[1].price == D(150)
    assert result.max_drawdown == D("0.25")
    assert result.final_equity == D(1500)
    assert len(result.fills) == 2


def test_dust_is_preserved_and_marked_without_imaginary_sale_fee() -> None:
    bars = [candle(i) for i in range(60)] + [candle(60), candle(61, "1")]
    result = run_backtest(
        bars,
        START + timedelta(hours=60),
        D(100),
        costs(min_notional=D(50), sell_fee=D("0.1")),
        "buy-hold",
    )
    assert result.btc == 1
    assert result.final_equity == 1
    assert result.total_fees == 0
    assert result.rejections[-1].reason == "below_minimum"


def test_future_changes_do_not_change_prior_fills() -> None:
    prefix = [candle(i, "100" if i < 40 else "110") for i in range(62)]
    a = run_backtest(
        [*prefix, candle(62, "90")], START + timedelta(hours=60), D(1000), costs(), "sma-trend-v0"
    )
    b = run_backtest(
        [*prefix, candle(62, "150")], START + timedelta(hours=60), D(1000), costs(), "sma-trend-v0"
    )
    assert a.fills[0] == b.fills[0]


def test_evaluation_rejects_missing_warmup_and_gaps() -> None:
    with pytest.raises(ValueError, match="warmup"):
        run_backtest([candle(i) for i in range(3)], START, D(100), costs(), "cash")
    with pytest.raises(ValueError, match="quality"):
        run_backtest(
            [candle(i) for i in range(62) if i != 61] + [candle(62)],
            START + timedelta(hours=60),
            D(100),
            costs(),
            "cash",
        )


@pytest.mark.parametrize("status", [429, 418, 500])
def test_collector_http_failure_is_not_empty_success(tmp_path: Path, status: int) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "test"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            collect(
                client, START, START + timedelta(hours=1), tmp_path / "bad", sleep=lambda _: None
            )
    assert calls == 1
    assert "failed" in (tmp_path / "bad/quality.json").read_text()


def test_empty_response_and_missing_bar_produce_quality_failure(tmp_path: Path) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    ) as client:
        with pytest.raises(ValueError, match="quality"):
            collect(
                client, START, START + timedelta(hours=1), tmp_path / "empty", sleep=lambda _: None
            )
    assert "2025-01-01T00:00:00Z" in (tmp_path / "empty/quality.json").read_text()


def test_remaining_request_header_adds_wait(tmp_path: Path) -> None:
    waits: list[float] = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=[raw(0)], headers={"Remaining-Req": "group=candle; min=1800; sec=0"}
            )
        )
    ) as client:
        collect(client, START, START + timedelta(hours=1), tmp_path / "data", sleep=waits.append)
    assert waits == [0.2, 1]


@pytest.mark.parametrize("capital", ["0", "-1", "NaN", "Infinity"])
def test_invalid_capital_rejected(capital: str) -> None:
    with pytest.raises(ValueError, match="capital"):
        run_backtest(
            [candle(i) for i in range(61)], START + timedelta(hours=60), D(capital), costs(), "cash"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("buy_fee", "-1"),
        ("sell_fee", "1"),
        ("buy_slippage", "NaN"),
        ("sell_slippage", "Infinity"),
        ("min_notional", "0"),
        ("quantity_step", "0"),
    ],
)
def test_invalid_costs_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Costs.model_validate(costs().model_dump() | {field: value})


def test_exact_sma_entry_boundary_does_not_buy() -> None:
    # slow=1000, fast=1002 => exactly 0.2%, not strictly greater.
    assert sma_target([D(999)] * 40 + [D(1002)] * 20, False) is None


def test_last_bar_signal_is_never_filled() -> None:
    bars = [candle(i) for i in range(60)] + [candle(60, "100", "200")]
    result = run_backtest(bars, START + timedelta(hours=60), D(1000), costs(), "sma-trend-v0")
    assert result.fills == []
    assert result.final_equity == 1000


def test_signal_sell_and_buy_rejection() -> None:
    bars = [candle(i, "100" if i < 40 else "110") for i in range(60)]
    bars.extend(candle(i, "109") for i in range(60, 120))
    result = run_backtest(bars, START + timedelta(hours=60), D(1000), costs(), "sma-trend-v0")
    assert result.fills[1].reason == "signal"
    assert result.natural_exits == 1
    small = run_backtest(
        bars, START + timedelta(hours=60), D(1), costs(min_notional=D(5000)), "sma-trend-v0"
    )
    assert not small.fills
    assert small.cash == 1


def test_delayed_signal_waits_one_more_bar_and_does_not_duplicate() -> None:
    bars = [candle(i, "100" if i < 40 else "110") for i in range(60)]
    bars += [candle(60, "120"), candle(61, "125"), candle(62, "126")]
    result = run_backtest(
        bars, START + timedelta(hours=60), D(1000), costs(), "sma-trend-v0", extra_delay_bars=1
    )
    assert result.fills[0].price == 125
    assert result.fills[0].time == bars[61].open_time
    assert len([fill for fill in result.fills if fill.side == "buy"]) == 1


def test_delay_stress_also_delays_risk_exit_without_rescheduling_forever() -> None:
    bars = [candle(i, "100" if i < 40 else "110") for i in range(60)]
    bars += [
        candle(60, "110"),
        candle(61, "110", "200"),
        candle(62, "200", "175"),
        candle(63, "150"),
        candle(64, "100"),
    ]
    result = run_backtest(
        bars, START + timedelta(hours=60), D(1100), costs(), "sma-trend-v0", extra_delay_bars=1
    )
    assert result.halted
    assert result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[64].open_time
    assert result.final_equity == 1000


def test_validation_rejects_invalid_ranges_and_naive_metadata() -> None:
    with pytest.raises(ValueError, match="end"):
        validate_candles([], START, START)
    with pytest.raises(ValueError, match="as_of"):
        validate_candles([], START, START + timedelta(hours=1), as_of=START.replace(tzinfo=None))
    with pytest.raises(ValueError, match="fetched_at"):
        Candle.model_validate(candle(0).model_dump() | {"fetched_at": START.replace(tzinfo=None)})


def test_validation_counts_outside_range() -> None:
    result, report = validate_candles([candle(0), candle(1)], START, START + timedelta(hours=1))
    assert len(result) == 1
    assert report.outside_range == 1


def test_invalid_simulation_options() -> None:
    with pytest.raises(ValueError, match="empty"):
        run_backtest([], START, D(100), costs(), "cash")
    with pytest.raises(ValueError, match="extra_delay"):
        run_backtest([candle(0)], START, D(100), costs(), "cash", extra_delay_bars=-1)
    with pytest.raises(ValueError, match="strategy"):
        run_backtest([candle(0)], START, D(100), costs(), "bad")  # type: ignore[arg-type]


def test_future_collection_range_is_rejected_before_any_request(tmp_path: Path) -> None:
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="require"):
            collect(client, START, START, tmp_path / "data")
    assert not (tmp_path / "data").exists()
