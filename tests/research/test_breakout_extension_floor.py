from decimal import Decimal

import pytest
from test_breakout_entry_stop_trend_context import MODES, long_decline
from test_breakout_exit_checkpoint import checkpoint_path

from evergreen.research import backtest
from evergreen.research.experiments.breakout_meta import costs, evaluate


def simulate(bars, *, delay=0, capital=Decimal(1000000)):
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        capital,
        costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        breakout_extension_floor=True,
        **MODES,
    )


def below(bars, *indices):
    for i in indices:
        bars[i] = bars[i].model_copy(update={"close": Decimal(102), "low": Decimal(101)})


@pytest.mark.parametrize("delay", [0, 1])
def test_two_new_closes_below_frozen_decision_price(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    below(bars, 205, 206)
    bars[207] = bars[207].model_copy(update={"close": Decimal(108), "high": Decimal(109)})
    result = simulate(bars, delay=delay)
    assert result.fills[1].signal_time == bars[206].close_time
    assert result.fills[1].time == bars[207 + delay].open_time
    assert result.fills[1].reason == "signal"


@pytest.mark.parametrize("recovery", [103, 104])
def test_equal_or_higher_close_resets_counter_and_observation_continues(monkeypatch, recovery):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    below(bars, 205, 207, 208)
    bars[206] = bars[206].model_copy(update={"close": Decimal(recovery)})
    assert simulate(bars).fills[1].signal_time == bars[208].close_time


def test_single_breach_and_24h_equality_do_not_exit(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    below(bars, 205)
    result = simulate(bars)
    assert result.fills[1].reason == "settlement"


def test_long_context_already_exiting_is_not_extended(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = long_decline()
    assert simulate(bars).fills[1].signal_time == bars[204].close_time


def test_rejection_retries_latched_exit_even_after_recovery(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    below(bars, 205, 206)
    bars[207] = bars[207].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    result = simulate(bars, capital=Decimal(5100))
    assert result.rejections[0].time == bars[207].open_time
    assert result.fills[1].time == bars[208].open_time


def test_risk_replaces_pending_floor_exit(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    below(bars, 205, 206)
    bars[207] = bars[207].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[209].open_time


def test_actual_new_entry_resets_reference_counter_and_exit_latch(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    below(bars, 205, 206)
    bars[207] = bars[207].model_copy(update={"open": Decimal(110), "high": Decimal(111)})
    bars[249] = bars[249].model_copy(update={"close": Decimal(95), "low": Decimal(94)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(115),
                "high": Decimal(116),
                "low": Decimal(114),
                "close": Decimal(115),
            }
        )
    result = simulate(bars)
    assert len(result.fills) == 4
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[3].reason == "settlement"
    assert result.fills[2].quantity * result.fills[2].price > Decimal(1000000)


def test_floor_requires_exclusive_policy50_mode(tmp_path):
    bars = checkpoint_path()
    for extra in (
        {},
        {**MODES, "breakout_exit_checkpoint": True},
        {**MODES, "breakout_exit_higher_low": True},
        {**MODES, "exit_decisions": []},
    ):
        with pytest.raises(ValueError, match="연장 가격"):
            backtest.run_backtest(
                bars,
                bars[200].open_time,
                Decimal(1000000),
                costs(),
                "breakout-v1",
                breakout_extension_floor=True,
                **extra,
            )
    with pytest.raises(ValueError, match="연장 가격"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            extension_floor_models=("candidate",),
        )
