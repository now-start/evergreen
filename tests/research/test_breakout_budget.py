from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.research import backtest
from evergreen.research.backtest import Costs, _Portfolio, run_backtest
from evergreen.research.experiments.breakout_meta import costs


def test_channel_budget_round_trip_costs_quantity_and_account_peak():
    free = Costs(
        buy_fee=0, sell_fee=0, buy_slippage=0, sell_slippage=0, quantity_step=1, min_notional=1
    )
    portfolio = _Portfolio(Decimal(1000), free, True)
    assert portfolio.channel_entry_fits(Decimal(10), Decimal(9))
    assert not portfolio.channel_entry_fits(Decimal(10), Decimal("8.99"))
    portfolio.peak = Decimal(1050)
    assert not portfolio.channel_entry_fits(Decimal(10), Decimal(9))
    portfolio.peak = Decimal(1000)
    portfolio.cash = Decimal(950)
    assert portfolio.channel_entry_fits(Decimal(10), Decimal("9.5"))
    assert not portfolio.channel_entry_fits(Decimal(10), Decimal(9))
    charged = _Portfolio(Decimal(1000), costs(), True)
    assert not charged.channel_entry_fits(Decimal(10), Decimal(9))
    # Prediction exactly matches an actual round trip, including coarse quantity rounding.
    expected = _Portfolio(Decimal(1000), free, True)
    time = fixture()[0].open_time
    expected.trade("buy", Decimal(11), time, "signal", time)
    expected.trade("sell", Decimal(10), time, "signal", time)
    projected = _Portfolio(Decimal(1000), free, True)
    projected.peak = expected.cash / Decimal(".9")
    assert projected.channel_entry_fits(Decimal(11), Decimal(10))
    projected.peak += 1
    assert not projected.channel_entry_fits(Decimal(11), Decimal(10))


def test_budget_uses_previous_channel_preserves_timing_and_default():
    bars = fixture()
    start = bars[200].open_time
    args = (bars, start, Decimal(1000000), costs(), "breakout-v1")
    assert run_backtest(*args) == run_backtest(*args, breakout_risk_budget=False)
    assert not run_backtest(*args, breakout_risk_budget=True).fills
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    # Current low is deliberately tiny; only previous 48 lows should enter the budget.
    bars[199] = bars[199].model_copy(update={"low": Decimal(1)})
    for delay in (0, 1):
        result = run_backtest(*args, breakout_risk_budget=True, extra_delay_bars=delay)
        assert result.fills[0].time == bars[200 + delay].open_time
    first = run_backtest(*args, breakout_risk_budget=True).fills[0]
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(10000)})
    assert run_backtest(*args, breakout_risk_budget=True).fills[0] == first


def test_budget_cannot_be_applied_to_unrelated_strategies_or_fast_exit():
    bars = fixture()
    with pytest.raises(ValueError, match="위험 여유"):
        run_backtest(
            bars, bars[200].open_time, Decimal(1000000), costs(), "cash", breakout_risk_budget=True
        )
    with pytest.raises(ValueError, match="위험 여유"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_risk_budget=True,
            breakout_exit_lookback=24,
        )


def test_budget_retains_loss_and_peak_for_later_entry(monkeypatch):
    bars = [
        b.model_copy(
            update={
                "open": Decimal(100),
                "high": Decimal(101),
                "low": Decimal(95),
                "close": Decimal(100),
            }
        )
        for b in fixture()
    ]
    bars[210] = bars[210].model_copy(update={"close": Decimal(94), "low": Decimal(94)})
    bars[211] = bars[211].model_copy(update={"open": Decimal(94), "low": Decimal(94)})
    entries = {bars[199].close_time, bars[220].close_time}

    def signals(history, holding, strategy, **kwargs):
        t = history[-1].close_time
        if not holding and t in entries:
            return "buy"
        return "sell" if holding and t == bars[210].close_time else None

    monkeypatch.setattr(backtest, "signal_target", signals)
    free = Costs(
        buy_fee=0,
        sell_fee=0,
        buy_slippage=0,
        sell_slippage=0,
        min_notional=1,
        quantity_step=Decimal(".00000001"),
    )
    result = run_backtest(
        bars, bars[200].open_time, Decimal(1000), free, "breakout-v1", breakout_risk_budget=True
    )
    assert [f.side for f in result.fills] == ["buy", "sell"]
    assert result.cash == 940 and not result.halted
    # A wrongly reset high-water mark would permit the second entry.
    restarted = run_backtest(
        bars, bars[221].open_time, Decimal(940), free, "breakout-v1", breakout_risk_budget=True
    )
    assert restarted.fills[0].side == "buy"
    assert restarted.fills[0].time == bars[221].open_time
