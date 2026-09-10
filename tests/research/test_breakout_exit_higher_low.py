from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pytest
from test_breakout_entry_stop_trend_context import MODES, long_decline
from test_breakout_exit_checkpoint import checkpoint_path

from evergreen.market import Candle
from evergreen.research import backtest
from evergreen.research.backtest import Result
from evergreen.research.experiments.breakout_meta import costs, evaluate


class InvalidOptions(TypedDict, total=False):
    breakout_entry_stop: bool
    breakout_entry_stop_adaptive_trail: bool
    breakout_entry_stop_budget: bool
    breakout_entry_stop_channel_reset: bool
    breakout_entry_stop_confirmations: int
    breakout_entry_stop_profit_trail: bool
    breakout_entry_stop_trend_confirmation: bool
    breakout_entry_stop_trend_lookback: int
    breakout_exit_checkpoint: bool


def simulate(bars: list[Candle], *, enabled: bool = True, delay: int = 0) -> Result:
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        breakout_exit_higher_low=enabled,
        **MODES,
    )


def higher_lows() -> list[Candle]:
    bars = checkpoint_path()
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_higher_low_extends_current_position_without_24h_checkpoint(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = higher_lows()
    control, result = simulate(bars, enabled=False, delay=delay), simulate(bars, delay=delay)
    assert result.fills[0] == control.fills[0]
    assert control.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].reason == "settlement"
    assert result.fills[1].time == bars[-1].close_time


@pytest.mark.parametrize("recent_low", [98, 99])
def test_equal_or_lower_low_keeps_policy50_sell(
    monkeypatch: pytest.MonkeyPatch, recent_low: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = higher_lows()
    bars[160] = bars[160].model_copy(update={"low": Decimal(99)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(recent_low)})
    assert simulate(bars) == simulate(bars, enabled=False)


def test_nonoverlapping_window_edges_and_current_bar_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = higher_lows()
    bars[160] = bars[160].model_copy(update={"low": Decimal(99)})
    bars[155] = bars[155].model_copy(update={"low": Decimal(80)})
    assert simulate(bars).fills[1].time == bars[205].open_time
    bars[156] = bars[156].model_copy(update={"low": Decimal(80)})
    bars[204] = bars[204].model_copy(update={"low": Decimal(70)})
    assert simulate(bars).fills[1].reason == "settlement"
    # The recent window starts at180; a low at179 belongs to the preceding window.
    bars[180] = bars[180].model_copy(update={"low": Decimal(79)})
    assert simulate(bars).fills[1].time == bars[205].open_time


def test_long_context_exit_still_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = long_decline()
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    assert simulate(bars) == simulate(bars, enabled=False)


def test_future_low_cannot_cancel_reserved_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    first = simulate(bars, delay=1)
    bars[205] = bars[205].model_copy(
        update={"low": Decimal(70), "high": Decimal(120), "close": Decimal(115)}
    )
    second = simulate(bars, delay=1)
    assert first.fills[:2] == second.fills[:2]


def test_risk_during_extension_is_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = higher_lows()
    bars[210] = bars[210].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars)
    assert result.fills[1].reason == "risk" and result.halted
    assert result.fills[1].time == bars[211].open_time


def test_option_is_exclusive_and_requires_policy50(tmp_path: Path) -> None:
    bars = higher_lows()
    invalid_options: tuple[InvalidOptions, ...] = (
        {},
        {"breakout_exit_checkpoint": True},
        {"breakout_entry_stop_trend_lookback": 168},
    )
    for extra in invalid_options[1:]:
        extra["breakout_entry_stop"] = MODES["breakout_entry_stop"]
        extra["breakout_entry_stop_confirmations"] = MODES["breakout_entry_stop_confirmations"]
        extra["breakout_entry_stop_channel_reset"] = MODES["breakout_entry_stop_channel_reset"]
        extra["breakout_entry_stop_profit_trail"] = MODES["breakout_entry_stop_profit_trail"]
        extra["breakout_entry_stop_adaptive_trail"] = MODES["breakout_entry_stop_adaptive_trail"]
        extra["breakout_entry_stop_trend_confirmation"] = MODES[
            "breakout_entry_stop_trend_confirmation"
        ]
        extra["breakout_entry_stop_budget"] = MODES["breakout_entry_stop_budget"]
    for extra in invalid_options:
        with pytest.raises(ValueError, match="저점 상승"):
            backtest.run_backtest(
                bars,
                bars[200].open_time,
                Decimal(1000000),
                costs(),
                "breakout-v1",
                breakout_exit_higher_low=True,
                **extra,
            )
    with pytest.raises(ValueError, match="저점 상승"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            exit_higher_low_models=("candidate",),
        )


def test_rejected_first_exit_does_not_become_a_new_extension_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = higher_lows()
    bars[180] = bars[180].model_copy(update={"low": Decimal(80)})
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    result = backtest.run_backtest(
        bars,
        bars[200].open_time,
        Decimal(5100),
        costs(),
        "breakout-v1",
        breakout_exit_higher_low=True,
        **MODES,
    )
    assert result.rejections[0].time == bars[205].open_time
    # At205 the low at180 moves into the preceding window, but approval cannot be retried.
    assert min(b.low for b in bars[181:205]) > min(b.low for b in bars[157:181])
    assert result.fills[1].time == bars[206].open_time


def test_long_context_history_is_required() -> None:
    bars = higher_lows()
    with pytest.raises(ValueError, match="192 hours"):
        backtest.run_backtest(
            bars,
            bars[191].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_exit_higher_low=True,
            **MODES,
        )
