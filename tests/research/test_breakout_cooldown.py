from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.research import backtest
from evergreen.research.experiments.breakout_meta import costs


@pytest.mark.parametrize("delay", [0, 1])
def test_cooldown_starts_at_filled_sell_and_allows_exact_expiry(monkeypatch, delay):
    bars = fixture()
    # Alternate signals isolate lifecycle timing from the channel entry rule.
    monkeypatch.setattr(
        backtest,
        "signal_target",
        lambda history, holding, strategy, **kw: "sell" if holding else "buy",
    )
    free = costs().model_copy(
        update={k: Decimal(0) for k in ("buy_fee", "sell_fee", "buy_slippage", "sell_slippage")}
    )
    args = (bars, bars[200].open_time, Decimal(1000000), free, "breakout-v1")
    default = backtest.run_backtest(*args, extra_delay_bars=delay)
    assert default == backtest.run_backtest(
        *args, extra_delay_bars=delay, breakout_cooldown_hours=0
    )
    result = backtest.run_backtest(*args, extra_delay_bars=delay, breakout_cooldown_hours=24)
    assert result.fills[:2] == default.fills[:2]
    sell, buy = result.fills[1:3]
    from datetime import timedelta

    assert buy.signal_time == sell.time + timedelta(hours=24)
    assert buy.time == sell.time + timedelta(hours=24 + delay)
    assert result.fills[3].side == "sell"  # Existing exits are not suppressed.
    for strategy, hours in (("cash", 24), ("breakout-v1", 12)):
        with pytest.raises(ValueError, match="대기"):
            backtest.run_backtest(*args[:-1], strategy, breakout_cooldown_hours=hours)
