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
        breakout_entry_path_context=dynamic,
        breakout_entry_stop_trend_lookback=lookback,
        **MODES,
    )


@pytest.mark.parametrize(
    "last,expected", [(D(100), 24), (D(99), 24), (D(104), 24), (D("104.00000001"), 168)]
)
def test_path_strict_half_boundary_and_nonpositive_advance(last, expected):
    bars = setup()[:25]
    # 100 -> 98 -> 100 -> last; at last=104, advance=4 and travel=8.
    bars[1] = bars[1].model_copy(update={"close": D(98), "low": D(97)})
    bars[-1] = bars[-1].model_copy(update={"close": last, "high": D(1000), "low": D(1)})
    assert backtest._entry_path_context(bars) == expected


def test_flat_and_monotonic_paths_and_exact_window():
    bars = setup()[:26]
    assert backtest._entry_path_context(bars) == 24
    bars[0] = bars[0].model_copy(update={"close": D(1)})
    bars[-1] = bars[-1].model_copy(update={"close": D(101)})
    assert backtest._entry_path_context(bars) == 168
    assert backtest._entry_path_context(bars[1:]) == 168
    with pytest.raises(ValueError, match="진입 경로"):
        backtest._entry_path_context(bars[:24])


@pytest.mark.parametrize("strong", [False, True])
@pytest.mark.parametrize("delay", [0, 1])
def test_original_signal_path_is_frozen_until_exit(monkeypatch, strong, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = checkpoint_path()
    if strong:
        bars[175] = bars[175].model_copy(update={"close": D(95), "low": D(94)})
    choose = backtest._entry_path_context
    seen = []

    def record(history):
        selected = choose(history)
        seen.append((history[-1].close_time, selected))
        return selected

    monkeypatch.setattr(backtest, "_entry_path_context", record)
    result = simulate(bars, delay=delay)
    selected = 168 if strong else 24
    assert seen == [(bars[199].close_time, selected)]
    assert result == simulate(bars, dynamic=False, lookback=selected, delay=delay)
    if delay == 0:
        assert result.fills[1].reason == ("settlement" if strong else "signal")


@pytest.mark.parametrize("capital,tr", [(D(4999), D(2)), (D(1000000), D(4))])
def test_rejected_buy_never_creates_path_context(monkeypatch, capital, tr):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: tr)
    monkeypatch.setattr(
        backtest, "_entry_path_context", lambda h: pytest.fail("거절된 매수의 상태 생성")
    )
    result = simulate(setup(), capital=capital)
    assert not result.fills and result.rejections


def test_path_keeps_preemptive_sell_retry(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(102), "low": D(101)})
    result = simulate(bars, capital=D(5100))
    assert result == simulate(bars, dynamic=False, lookback=168, capital=D(5100))
    assert result.rejections[0].side == "sell"
    assert result.fills[1].time == bars[205].open_time


def test_next_fill_reselects_context_with_preserved_capital(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(110), "high": D(111)})
    bars[249] = bars[249].model_copy(update={"close": D(95), "low": D(94)})
    bars[250] = bars[250].model_copy(update={"close": D(115), "high": D(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(115), "close": D(115), "high": D(116), "low": D(114)}
        )
    choose = backtest._entry_path_context
    seen = []

    def record(history):
        selected = choose(history)
        seen.append((history[-1].close_time, selected))
        return selected

    monkeypatch.setattr(backtest, "_entry_path_context", record)
    result = simulate(bars)
    assert seen == [(bars[199].close_time, 168), (bars[250].close_time, 24)]
    assert len(result.fills) == 4 and result.fills[-1].reason == "settlement"
    buy = result.fills[2]
    assert buy.price * buy.quantity + buy.fee > D(1000000)


def test_path_policy_rejects_mixed_mode_and_insufficient_warmup(tmp_path):
    bars = setup()
    with pytest.raises(ValueError, match="192 hours"):
        simulate(bars, start=bars[191].open_time)
    with pytest.raises(ValueError, match="진입 경로"):
        simulate(bars, lookback=168)
    with pytest.raises(ValueError, match="진입 경로"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            D(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_path_context=True,
            breakout_entry_context=True,
            breakout_liquidation_buffer=True,
            **MODES,
        )
    with pytest.raises(ValueError, match="진입 경로"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_path_context_models=("candidate",),
        )
