from __future__ import annotations

from decimal import Decimal
from decimal import Decimal as D
from pathlib import Path
from typing import TypedDict

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_trend_context import MODES
from test_breakout_exit_checkpoint import checkpoint_path

from evergreen.market import Candle
from evergreen.research import backtest
from evergreen.research.backtest import Costs, Result
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
    breakout_extension_floor: bool


def path() -> list[Candle]:
    bars = setup()
    bars[199] = bars[199].model_copy(update={"low": D(99)})
    bars[202] = bars[202].model_copy(update={"close": D(108), "high": D(109)})
    bars[203] = bars[203].model_copy(update={"close": D(99), "low": D(98)})
    return bars


def simulate(
    bars: list[Candle],
    *,
    enabled: bool = True,
    delay: int = 0,
    capital: Decimal = D(1000000),
    charges: Costs | None = None,
    lookback: int = 24,
) -> Result:
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        capital,
        charges or costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        breakout_liquidation_buffer=enabled,
        breakout_entry_stop_trend_lookback=lookback,
        **MODES,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_preemptive_exit_is_latched_through_rebound_without_halting(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    control, result = simulate(bars, enabled=False, delay=delay), simulate(bars, delay=delay)
    assert control.fills[1].time > result.fills[1].time
    assert result.fills[1].signal_time == bars[203].close_time
    assert result.fills[1].time == bars[204 + delay].open_time
    assert result.fills[1].reason == "signal"
    assert not result.halted


@pytest.mark.parametrize("close,exits", [("99.2", True), ("99.20000001", False)])
def test_exact_net_liquidation_boundary_includes_equality(
    monkeypatch: pytest.MonkeyPatch, close: str, exits: bool
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[203] = bars[203].model_copy(update={"close": D(close)})
    zero = costs().model_copy(
        update={k: D(0) for k in ("buy_fee", "sell_fee", "buy_slippage", "sell_slippage")}
    )
    result = simulate(bars, capital=D(1040000), charges=zero)
    assert (result.fills[1].time == bars[204].open_time) is exits


def test_known_delay_adds_one_more_prior_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[203] = bars[203].model_copy(update={"close": D("100.5")})
    bars[204] = bars[204].model_copy(update={"close": D(99), "low": D(98)})
    assert simulate(bars).fills[1].signal_time == bars[204].close_time
    assert simulate(bars, delay=1).fills[1].signal_time == bars[203].close_time


def test_current_low_does_not_enter_prior_range_or_move_order() -> None:
    bars = path()
    before = simulate(bars)
    bars[203] = bars[203].model_copy(update={"low": D(1)})
    after = simulate(bars)
    assert before.fills[1] == after.fills[1]
    assert after.fills[1].signal_time == bars[203].close_time


def test_rejected_preemptive_sell_retries_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(102), "low": D(101)})
    result = simulate(bars, capital=D(5100))
    assert result.rejections[0].time == bars[204].open_time
    assert result.fills[1].time == bars[205].open_time
    assert result.fills[1].signal_time == bars[204].close_time


@pytest.mark.parametrize("risk", [False, True])
def test_existing_earlier_sell_is_kept_unless_legacy_risk_overrides(
    monkeypatch: pytest.MonkeyPatch, risk: bool
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(".5"))
    bars = checkpoint_path()
    bars[205] = bars[205].model_copy(
        update={"close": D(70 if risk else 100), "low": D(69 if risk else 99)}
    )
    result = simulate(bars, delay=1)
    assert result.fills[1].time == bars[207 if risk else 206].open_time
    assert result.fills[1].signal_time == bars[205 if risk else 204].close_time
    assert result.fills[1].reason == ("risk" if risk else "signal")


def test_new_entry_resets_latch_but_retains_account_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(110), "high": D(111)})
    bars[249] = bars[249].model_copy(update={"close": D(95), "low": D(94)})
    bars[250] = bars[250].model_copy(update={"close": D(115), "high": D(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(115), "close": D(115), "high": D(116), "low": D(114)}
        )
    result = simulate(bars)
    assert len(result.fills) == 4
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[3].reason == "settlement"
    assert result.fills[2].quantity * result.fills[2].price + result.fills[2].fee > D(1000000)


def test_buffer_rejects_other_policies_and_extension_combinations(tmp_path: Path) -> None:
    bars = path()
    invalid_options: tuple[InvalidOptions, ...] = (
        {},
        {"breakout_extension_floor": True},
        {"breakout_entry_stop_trend_lookback": 48},
    )
    for options in invalid_options[1:]:
        options["breakout_entry_stop"] = MODES["breakout_entry_stop"]
        options["breakout_entry_stop_confirmations"] = MODES["breakout_entry_stop_confirmations"]
        options["breakout_entry_stop_channel_reset"] = MODES["breakout_entry_stop_channel_reset"]
        options["breakout_entry_stop_profit_trail"] = MODES["breakout_entry_stop_profit_trail"]
        options["breakout_entry_stop_adaptive_trail"] = MODES["breakout_entry_stop_adaptive_trail"]
        options["breakout_entry_stop_trend_confirmation"] = MODES[
            "breakout_entry_stop_trend_confirmation"
        ]
        options["breakout_entry_stop_budget"] = MODES["breakout_entry_stop_budget"]
    for options in invalid_options:
        with pytest.raises(ValueError, match="청산 여유"):
            backtest.run_backtest(
                bars,
                bars[200].open_time,
                D(1000000),
                costs(),
                "breakout-v1",
                breakout_liquidation_buffer=True,
                **options,
            )
    with pytest.raises(ValueError, match="청산 여유"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            liquidation_buffer_models=("candidate",),
        )


@pytest.mark.parametrize("delay", [0, 1])
def test_long_context_keeps_same_preemptive_guard(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    result = simulate(bars, lookback=168, delay=delay)
    assert result == simulate(bars, lookback=24, delay=delay)
    assert result.fills[1].signal_time == bars[203].close_time


def test_long_context_retains_trend_when_buffer_has_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = checkpoint_path()
    result = simulate(bars, lookback=168)
    assert result == simulate(bars, lookback=168, enabled=False)
    assert result.fills[1].reason == "settlement"
    assert simulate(bars, lookback=24).fills[1].time == bars[205].open_time


def test_long_context_guard_retries_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(102), "low": D(101)})
    result = simulate(bars, lookback=168, capital=D(5100))
    assert result == simulate(bars, capital=D(5100))
    assert result.rejections[0].time == bars[204].open_time
    assert result.fills[1].time == bars[205].open_time


def test_long_context_guard_resets_on_next_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(110), "high": D(111)})
    bars[249] = bars[249].model_copy(update={"close": D(95), "low": D(94)})
    bars[250] = bars[250].model_copy(update={"close": D(115), "high": D(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(115), "close": D(115), "high": D(116), "low": D(114)}
        )
    result = simulate(bars, lookback=168)
    assert result == simulate(bars)
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[3].reason == "settlement"
