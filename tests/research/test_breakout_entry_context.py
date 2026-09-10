from decimal import Decimal as D

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_trend_context import MODES
from test_breakout_exit_checkpoint import checkpoint_path
from test_breakout_liquidation_buffer import path

from evergreen.research import backtest
from evergreen.research.experiments.breakout_meta import costs, evaluate


def simulate(bars, *, dynamic=True, lookback=24, delay=0, capital=D(1000000), start=None):
    return backtest.run_backtest(
        bars,
        start or bars[200].open_time,
        capital,
        costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        breakout_liquidation_buffer=True,
        breakout_entry_context=dynamic,
        breakout_entry_stop_trend_lookback=lookback,
        **MODES,
    )


@pytest.mark.parametrize(
    "advance,expected", [(D(-1), 24), (D(0), 24), (D(6), 24), (D("6.00000001"), 168)]
)
def test_context_strict_boundary_and_excludes_signal_tr(advance, expected):
    bars = setup()[:26]
    bars[-1] = bars[-1].model_copy(update={"close": D(100) + advance, "high": D(1000), "low": D(1)})
    assert backtest._entry_trend_context(bars) == expected


def test_context_rejects_missing_history():
    with pytest.raises(ValueError, match="진입 맥락"):
        backtest._entry_trend_context(setup()[:25])


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("strong", [False, True])
def test_signal_context_is_fixed_through_holding_and_delayed_fill(monkeypatch, delay, strong):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = checkpoint_path()
    # Only the frozen signal's s-24 endpoint differs; later closes recover.
    if strong:
        bars[175] = bars[175].model_copy(update={"close": D(95), "low": D(94)})
    chosen = 168 if strong else 24
    choose = backtest._entry_trend_context
    seen = []

    def record(history):
        result = choose(history)
        seen.append((history[-1].close_time, result))
        return result

    monkeypatch.setattr(backtest, "_entry_trend_context", record)
    result = simulate(bars, delay=delay)
    assert seen == [(bars[199].close_time, chosen)]
    assert result == simulate(bars, dynamic=False, lookback=chosen, delay=delay)
    assert result.fills[0].signal_time == bars[199].close_time
    assert result.fills[0].time == bars[200 + delay].open_time
    if strong and delay == 0:
        assert result.fills[1].reason == "settlement"
    elif not strong and delay == 0:
        assert result.fills[1].signal_time == bars[204].close_time


def test_context_keeps_preemptive_guard_and_rejected_sell_latch(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[175] = bars[175].model_copy(update={"close": D(95), "low": D(94)})
    bars[204] = bars[204].model_copy(update={"open": D(102), "low": D(101)})
    result = simulate(bars, capital=D(5100))
    assert result == simulate(bars, dynamic=False, lookback=168, capital=D(5100))
    assert result.rejections[0].side == "sell"
    assert result.fills[1].time == bars[205].open_time


def test_context_requires_long_warmup_and_original_short_buffer_policy(tmp_path):
    bars = setup()
    with pytest.raises(ValueError, match="192 hours"):
        simulate(bars, start=bars[191].open_time)
    with pytest.raises(ValueError, match="진입 맥락"):
        simulate(bars, lookback=168)
    with pytest.raises(ValueError, match="진입 맥락"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            D(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_context=True,
        )
    with pytest.raises(ValueError, match="진입 맥락"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_context_models=("candidate",),
        )


def test_budget_rejection_does_not_override_initial_safety(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(4))
    bars = setup()
    monkeypatch.setattr(
        backtest, "_entry_trend_context", lambda h: pytest.fail("거절된 매수의 상태 생성")
    )
    result = simulate(bars)
    assert not result.fills
    assert result.rejections[0].reason == "risk_budget"
    assert result == simulate(bars, dynamic=False)


def test_minimum_buy_rejection_does_not_create_context(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    monkeypatch.setattr(
        backtest, "_entry_trend_context", lambda h: pytest.fail("거절된 매수의 상태 생성")
    )
    result = simulate(setup(), capital=D(4999))
    assert not result.fills and result.rejections


def test_next_filled_entry_chooses_new_context_without_resetting_capital(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(110), "high": D(111)})
    bars[249] = bars[249].model_copy(update={"close": D(95), "low": D(94)})
    bars[250] = bars[250].model_copy(update={"close": D(115), "high": D(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(115), "close": D(115), "high": D(116), "low": D(114)}
        )
    choose = backtest._entry_trend_context
    decisions = []

    def record(history):
        lookback = choose(history)
        decisions.append((history[-1].close_time, lookback))
        return lookback

    monkeypatch.setattr(backtest, "_entry_trend_context", record)
    result = simulate(bars)
    assert decisions == [(bars[199].close_time, 24), (bars[250].close_time, 168)]
    assert len(result.fills) == 4 and result.fills[-1].reason == "settlement"
    buy = result.fills[2]
    assert buy.price * buy.quantity + buy.fee > D(1000000)
