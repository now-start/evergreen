from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.strategies import simple_rsi, strategy_target, warmup_bars

D = Decimal
START = datetime(2024, 1, 1, tzinfo=UTC)


def bars(count: int, last: str = "100") -> list[Candle]:
    return [
        Candle(
            open_time=START + timedelta(hours=i),
            open=D(100),
            high=max(D(100), D(last)) if i == count - 1 else D(100),
            low=min(D(100), D(last)) if i == count - 1 else D(100),
            close=D(last) if i == count - 1 else D(100),
            volume=D(1),
            quote_volume=D(100),
            fetched_at=START + timedelta(days=100),
        )
        for i in range(count)
    ]


def test_slow_sma_requires_full_window_and_separate_exit() -> None:
    assert warmup_bars("sma-slow-v1") == 168
    assert strategy_target(bars(167, "200"), False, "sma-slow-v1") is None
    assert strategy_target(bars(168, "200"), False, "sma-slow-v1") == "buy"
    assert strategy_target(bars(168, "90"), True, "sma-slow-v1") == "sell"


def test_breakout_excludes_current_bar_from_channel() -> None:
    assert warmup_bars("breakout-v1") == 169
    assert strategy_target(bars(168, "200"), False, "breakout-v1") is None
    assert strategy_target(bars(169, "101"), False, "breakout-v1") == "buy"
    assert strategy_target(bars(169, "100.1"), False, "breakout-v1") is None
    assert strategy_target(bars(169, "99"), True, "breakout-v1") == "sell"
    assert strategy_target(bars(169, "100"), True, "breakout-v1") is None


def test_new_strategy_keeps_next_open_and_risk_limit() -> None:
    candles = bars(169, "101")
    candles += [
        Candle(
            open_time=START + timedelta(hours=169),
            open=D(110),
            high=D(110),
            low=D(90),
            close=D(90),
            volume=D(1),
            quote_volume=D(100),
            fetched_at=START + timedelta(days=100),
        ),
        Candle(
            open_time=START + timedelta(hours=170),
            open=D(80),
            high=D(80),
            low=D(80),
            close=D(80),
            volume=D(1),
            quote_volume=D(100),
            fetched_at=START + timedelta(days=100),
        ),
    ]
    costs = Costs(
        buy_fee=D(0),
        sell_fee=D(0),
        buy_slippage=D(0),
        sell_slippage=D(0),
        min_notional=D(1),
        quantity_step=D("0.00000001"),
    )
    result = run_backtest(candles, START + timedelta(hours=169), D(1100), costs, "breakout-v1")
    assert result.fills[0].price == 110
    assert result.halted
    assert result.fills[1].reason == "risk"
    assert result.final_equity == 800


def from_closes(values: list[Decimal]) -> list[Candle]:
    return [
        bar.model_copy(update={"open": value, "high": value, "low": value, "close": value})
        for bar, value in zip(bars(len(values)), values, strict=True)
    ]


def test_price_band_uses_prior_population_std_and_strict_entry() -> None:
    previous = [D(99), D(101)] * 12
    assert warmup_bars("band-reversion-v1") == 25
    assert strategy_target(from_closes(previous), False, "band-reversion-v1") is None
    assert strategy_target(from_closes([*previous, D(98)]), False, "band-reversion-v1") is None
    assert (
        strategy_target(from_closes([*previous, D("97.99")]), False, "band-reversion-v1") == "buy"
    )
    assert strategy_target(from_closes([*previous, D(100)]), True, "band-reversion-v1") == "sell"
    assert strategy_target(from_closes([*previous, D(99)]), True, "band-reversion-v1") is None
    assert strategy_target(bars(25, "90"), False, "band-reversion-v1") is None


def test_simple_rsi_handles_zero_gain_loss_and_known_ratio() -> None:
    with pytest.raises(ValueError, match="15개"):
        simple_rsi([D(100)] * 14)
    assert simple_rsi([D(100)] * 15) == 50
    assert simple_rsi([D(i) for i in range(15)]) == 100
    assert simple_rsi([D(i) for i in reversed(range(15))]) == 0
    assert simple_rsi([D(100)] * 13 + [D(110), D(105)]) == D(100) * 10 / 15


def test_rsi_rebound_requires_crossing_and_long_term_filter() -> None:
    tail = [D(110)] + [D(100)] * 13 + [D(101), D(105)]
    rising = from_closes([D(100)] * 152 + tail)
    assert warmup_bars("rsi-rebound-v1") == 168
    assert strategy_target(rising[:-1], False, "rsi-rebound-v1") is None
    assert strategy_target(rising, False, "rsi-rebound-v1") == "buy"
    assert strategy_target(rising, True, "rsi-rebound-v1") == "sell"
    falling = from_closes([D(200)] * 152 + tail)
    assert strategy_target(falling, False, "rsi-rebound-v1") is None
    assert strategy_target(bars(168, "90"), True, "rsi-rebound-v1") == "sell"
    assert strategy_target(bars(168), False, "rsi-rebound-v1") is None
    assert strategy_target(bars(168), True, "rsi-rebound-v1") is None
