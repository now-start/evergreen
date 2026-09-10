from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pytest
from test_breakout_exit import fixture
from test_breakout_trailing import trailing_bars

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.strategies import Strategy


class TrailingOptions(TypedDict):
    breakout_trailing_exit: bool
    extra_delay_bars: int


class DelayedAdaptiveOptions(TypedDict):
    breakout_trailing_adaptive: bool
    breakout_trailing_exit: bool
    breakout_trailing_profit_only: bool
    extra_delay_bars: int


class AdaptiveOptions(TypedDict):
    breakout_trailing_exit: bool
    breakout_trailing_profit_only: bool
    breakout_trailing_adaptive: bool


def profit_bars() -> list[Candle]:
    bars = trailing_bars()
    # Buy104 + distance7.125: equality activates; close104 is then exit equality.
    bars[230] = bars[230].model_copy(update={"close": Decimal("111.125"), "high": Decimal(112)})
    bars[231] = bars[231].model_copy(update={"close": Decimal(104)})
    bars[232] = bars[232].model_copy(update={"close": Decimal("103.9")})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_profit_activation_boundary_persists_after_pullback_and_is_causal(delay: int) -> None:
    bars = profit_bars()
    if delay:
        bars[200] = bars[200].model_copy(update={"close": Decimal(120)})
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    options: TrailingOptions = {"breakout_trailing_exit": True, "extra_delay_bars": delay}
    original = run_backtest(*args, **options)
    assert original == run_backtest(*args, **options, breakout_trailing_profit_only=False)
    result = run_backtest(*args, **options, breakout_trailing_profit_only=True)
    assert result.fills[0] == original.fills[0]
    assert result.fills[1].time == bars[233 + delay].open_time
    assert original.fills[1].time < result.fills[1].time
    assert result.fills[1].reason == "signal"
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(10000)})
    assert (
        run_backtest(*args, **options, breakout_trailing_profit_only=True).fills[:2]
        == result.fills[:2]
    )


def test_profit_activation_uses_actual_delayed_entry_price_not_signal_or_prior_open() -> None:
    bars = profit_bars()
    bars[201] = bars[201].model_copy(update={"open": Decimal(105)})
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        extra_delay_bars=1,
        breakout_trailing_exit=True,
        breakout_trailing_profit_only=True,
    )
    assert result.fills[0].reference_price == 105
    # Peak111.125 is below actual buy105 +7.125, so no added exit at232.
    assert len(result.fills) == 2 and result.fills[1].reason == "settlement"


@pytest.mark.parametrize("adaptive", [False, True])
def test_profit_activation_resets_after_new_buy(adaptive: bool) -> None:
    bars = profit_bars()
    if adaptive:
        # Keep current range below the entry floor so both modes sell at233.
        for i in range(200, 230):
            bars[i] = bars[i].model_copy(
                update=dict.fromkeys(("open", "high", "low", "close"), Decimal(104))
            )
        bars[230] = bars[230].model_copy(update={"high": Decimal("111.125"), "low": Decimal(104)})
        bars[231] = bars[231].model_copy(update={"high": Decimal(104), "low": Decimal(104)})
    bars[233] = bars[233].model_copy(
        update={"open": Decimal(112), "close": Decimal(112), "high": Decimal(113)}
    )
    bars += [
        bars[-1].model_copy(update={"open_time": bars[-1].open_time + timedelta(hours=i)})
        for i in range(1, 211)
    ]
    bars[380] = bars[380].model_copy(update={"low": Decimal(80)})
    bars[419] = bars[419].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    bars[420] = bars[420].model_copy(
        update={"open": Decimal(106), "close": Decimal(106), "high": Decimal(107)}
    )
    bars[421] = bars[421].model_copy(update={"close": Decimal(108), "high": Decimal(109)})
    bars[422] = bars[422].model_copy(update={"close": Decimal(99), "low": Decimal(98)})
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_trailing_exit=True,
        breakout_trailing_profit_only=True,
        breakout_trailing_adaptive=adaptive,
    )
    assert [(f.side, f.time) for f in result.fills[:3]] == [
        ("buy", bars[200].open_time),
        ("sell", bars[233].open_time),
        ("buy", bars[420].open_time),
    ]
    assert len(result.fills) == 4 and result.fills[3].reason == "settlement" and not result.halted


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("adaptive", [False, True])
def test_unarmed_profit_trailing_keeps_channel_exit_and_risk(delay: int, adaptive: bool) -> None:
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(1)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    options: DelayedAdaptiveOptions = {
        "breakout_trailing_exit": True,
        "breakout_trailing_profit_only": True,
        "extra_delay_bars": delay,
        "breakout_trailing_adaptive": adaptive,
    }
    result = run_backtest(*args, **options)
    assert (
        result.fills[1].time == bars[251 + delay].open_time and result.fills[1].reason == "signal"
    )
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = run_backtest(*args, **options)
    assert (
        result.halted
        and result.fills[1].reason == "risk"
        and result.fills[1].time == bars[251 + delay].open_time
    )


@pytest.mark.parametrize("adaptive", [False, True])
def test_profit_trailing_zero_range_and_rejected_buy(adaptive: bool) -> None:
    bars = fixture()
    for i in range(199):
        bars[i] = bars[i].model_copy(
            update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100))
        )
    bars[250] = bars[250].model_copy(update={"close": Decimal(99), "low": Decimal(98)})
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    options: AdaptiveOptions = {
        "breakout_trailing_exit": True,
        "breakout_trailing_profit_only": True,
        "breakout_trailing_adaptive": adaptive,
    }
    assert run_backtest(*args) == run_backtest(*args, **options)
    small = run_backtest(
        bars, bars[200].open_time, Decimal(1000), costs(), "breakout-v1", **options
    )
    assert small.rejections and not small.fills and small.cash == 1000


def test_profit_trailing_requires_trailing_mode_and_cannot_mix_runner_modes(tmp_path: Path) -> None:
    bars = fixture()
    with pytest.raises(ValueError, match="활성"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_trailing_profit_only=True,
        )
    with pytest.raises(ValueError, match="동시"):
        breakout_confirmation.run_study(
            tmp_path, tmp_path, tmp_path / "out", trailing_exit=True, profit_trailing_exit=True
        )
    with pytest.raises(ValueError, match="활성"):
        evaluate(
            {"breakout-v1": [], "cash": [], "prior": [], "candidate": []},
            tmp_path,
            models=("candidate",),
            trailing_profit_only_models=("candidate",),
        )
    assert not (tmp_path / "out").exists()
