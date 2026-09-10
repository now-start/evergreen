from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast

import pytest
from test_breakout_exit import fixture

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.experiments.breakout_meta import costs
from evergreen.strategies import Strategy


def test_failure_exit_is_anchored_excludes_signal_bar_and_respects_delay() -> None:
    bars = fixture()
    bars[200] = bars[200].model_copy(update={"high": Decimal(200)})
    bars[205] = bars[205].model_copy(update={"close": Decimal(101), "low": Decimal(100)})
    bars[210] = bars[210].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[211] = bars[211].model_copy(update={"open": Decimal(100), "low": Decimal(99)})
    start = bars[200].open_time
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        start,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    assert run_backtest(*args) == run_backtest(*args, breakout_failure_exit=False)
    for delay in (0, 1):
        original = run_backtest(*args, extra_delay_bars=delay)
        result = run_backtest(*args, breakout_failure_exit=True, extra_delay_bars=delay)
        assert result.fills[0] == original.fills[0]
        assert result.fills[1].time == bars[211 + delay].open_time
        assert result.fills[1].reason == "signal"
        assert result.fills[1].reference_price == bars[211 + delay].open
    before = run_backtest(*args, breakout_failure_exit=True).fills[:2]
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(10000)})
    assert run_backtest(*args, breakout_failure_exit=True).fills[:2] == before


def test_failure_exit_resets_anchor_after_new_buy() -> None:
    bars = fixture()
    bars[200] = bars[200].model_copy(update={"high": Decimal(200)})
    bars[210] = bars[210].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[211] = bars[211].model_copy(update={"open": Decimal(100), "low": Decimal(99)})
    bars[220] = bars[220].model_copy(update={"close": Decimal(201), "high": Decimal(202)})
    bars[221] = bars[221].model_copy(
        update={
            "open": Decimal(202),
            "close": Decimal(203),
            "high": Decimal(204),
            "low": Decimal(201),
        }
    )
    bars[222] = bars[222].model_copy(
        update={
            "open": Decimal(203),
            "close": Decimal(199),
            "high": Decimal(204),
            "low": Decimal(198),
        }
    )
    bars[223] = bars[223].model_copy(
        update={
            "open": Decimal(199),
            "close": Decimal(199),
            "high": Decimal(200),
            "low": Decimal(198),
        }
    )
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
    )
    assert [(f.side, f.time) for f in result.fills] == [
        ("buy", bars[200].open_time),
        ("sell", bars[211].open_time),
        ("buy", bars[221].open_time),
        ("sell", bars[223].open_time),
    ]


def test_failure_exit_rejects_unrelated_and_combined_options() -> None:
    bars = fixture()
    for strategy, extra in (
        ("cash", {}),
        ("breakout-v1", {"breakout_risk_budget": True}),
        ("breakout-v1", {"breakout_exit_lookback": 24}),
        ("breakout-v1", {"breakout_cooldown_hours": 24}),
    ):
        with pytest.raises(ValueError, match="돌파 실패"):
            run_backtest(
                bars,
                bars[200].open_time,
                Decimal(1000000),
                costs(),
                cast(Strategy, strategy),
                breakout_failure_exit=True,
                **extra,
            )


def test_failure_exit_keeps_original_channel_exit_and_rejected_buys_flat() -> None:
    bars = fixture()
    bars[225] = bars[224].model_copy(update={"open_time": bars[225].open_time})
    bars[226] = bars[224].model_copy(update={"open_time": bars[226].open_time})
    # Above frozen entry ceiling 101, but below the rolling 48-hour floor 103.
    bars[250] = bars[250].model_copy(update={"close": Decimal(102), "low": Decimal(101)})
    for delay in (0, 1):
        result = run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_failure_exit=True,
            extra_delay_bars=delay,
        )
        assert result.fills[1].time == bars[251 + delay].open_time
        assert result.fills[1].reason == "signal"
    rejected = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
    )
    assert rejected.rejections and not rejected.fills
    assert rejected.cash == Decimal(1000) and rejected.btc == 0
