"""Offline smoke test: no network, no numpy/pandas. Runs as a plain script
(``python3 evergreen_lab/tests/test_smoke.py``) or under pytest.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone

# Make the package importable when run as a plain script from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from evergreen_lab import Action, BarContext, Candle, Cost, Strategy, create, list_strategies, run_backtest
from evergreen_lab.core.registry import register

VERSIONS = ("v6", "v1", "v2", "v3", "v4", "v5")


def synthetic_candles(n: int = 300, seed_price: float = 30_000_000.0) -> list[Candle]:
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    bars: list[Candle] = []
    price = seed_price
    state = 123_456_789
    for i in range(n):
        state = (1_103_515_245 * state + 12_345) & 0x7FFFFFFF
        noise = (state / 0x7FFFFFFF) - 0.5
        drift = math.sin(i / 18.0) * 0.012
        ret = drift + (noise * 0.02)
        open_price = price
        close_price = max(1.0, price * (1.0 + ret))
        high = max(open_price, close_price) * (1.0 + abs(noise) * 0.01)
        low = min(open_price, close_price) * (1.0 - abs(noise) * 0.01)
        bars.append(Candle(start + timedelta(hours=4 * i), open_price, high, low, close_price, 100.0 + state % 1000))
        price = close_price
    return bars


def test_all_versions_registered() -> None:
    for name in VERSIONS:
        assert name in list_strategies(), name


def test_backtest_runs_and_is_spot_consistent() -> None:
    result = run_backtest(create("v6"), synthetic_candles(), Cost())
    assert len(result.rows) == 300
    assert result.rows[-1].action == "HOLD", "final bar cannot be executed, must be HOLD"
    for row in result.rows:
        assert row.target_ratio in (0.0, 1.0), "spot position must be flat or fully invested"
        assert math.isfinite(row.equity) and row.equity > 0.0


def test_every_registered_strategy_runs_and_is_spot() -> None:
    candles = synthetic_candles(400)
    for name in VERSIONS:
        result = run_backtest(create(name), candles, Cost())
        assert len(result.rows) == 400, name
        assert result.rows[-1].action == "HOLD", name
        for row in result.rows:
            assert row.target_ratio in (0.0, 1.0), f"{name}: non-spot ratio"
            assert math.isfinite(row.equity) and row.equity > 0.0, name
        assert 0.0 <= result.summary.win_rate <= 1.0, name
        seen_buy = False
        for row in result.rows:
            if row.action == "BUY":
                seen_buy = True
            if row.action == "SELL":
                assert seen_buy, f"{name}: sell before any buy"


def test_reset_makes_reuse_deterministic() -> None:
    strat = create("v2")
    candles = synthetic_candles(400)
    first = run_backtest(strat, candles, Cost())
    second = run_backtest(strat, candles, Cost())  # same instance reused
    assert first.summary.total_return == second.summary.total_return


def test_no_lookahead_before_training_cut() -> None:
    candles = synthetic_candles()
    result = run_backtest(create("v6"), candles)
    cut = int(len(candles) * 0.6)
    for i, row in enumerate(result.rows):
        if i < cut:
            assert row.action == "HOLD", f"traded at bar {i} before training cut {cut}"


def test_hold_forever_returns_zero() -> None:
    @register("hold_forever_test")
    class _Hold(Strategy):
        def decide(self, ctx: BarContext) -> Action:
            return Action.HOLD

    result = run_backtest(create("hold_forever_test"), synthetic_candles())
    assert result.summary.round_trips == 0
    assert not result.summary.open_position
    assert abs(result.summary.total_return) < 1e-9


def test_analysis_grid_search_and_walk_forward() -> None:
    from evergreen_lab.analysis import grid_search, walk_forward

    candles = synthetic_candles(500)
    grid = {"buy_cutoff": [0.03, 0.055, 0.08], "sell_cutoff": [0.2, 0.25]}
    top = grid_search("v6", candles, grid, top_k=3)
    assert len(top) == 3
    assert top[0].score >= top[-1].score
    assert set(top[0].params) == {"buy_cutoff", "sell_cutoff"}

    wf = walk_forward("v6", candles, grid, train_size=250, test_size=60)
    assert len(wf.windows) >= 1
    assert len(wf.rows) > 0
    assert math.isfinite(wf.summary.total_return)



def test_buy_hold_captures_full_market() -> None:
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    opens = [100.0, 110.0, 121.0]  # two +10% open-to-open intervals
    candles = [Candle(start + timedelta(days=i), o, o, o, o, 1.0) for i, o in enumerate(opens)]

    class _Hold(Strategy):
        def decide(self, ctx: BarContext) -> Action:
            return Action.HOLD

    r = run_backtest(_Hold(), candles, Cost(fee_per_side=0.0, slippage=0.0))
    # buy & hold over the whole window must include the first interval
    assert abs(r.summary.buy_hold_return - (opens[-1] / opens[0] - 1.0)) < 1e-9


def test_initial_equity_is_normalized() -> None:
    candles = synthetic_candles(50)

    class _Hold(Strategy):
        def decide(self, ctx: BarContext) -> Action:
            return Action.HOLD

    r = run_backtest(_Hold(), candles, Cost(fee_per_side=0.0, slippage=0.0), initial_equity=1000.0)
    assert abs(r.summary.total_return) < 1e-9  # flat strategy => 0% regardless of starting capital



def test_regime_warmup_covers_atr_period() -> None:
    # tuning atr_period above regime_ema_len must still wait for ATR (trailing stop needs it)
    for name in ("v2", "v3", "v4", "v5"):
        strat = create(name, regime_ema_len=50, atr_period=120)
        assert strat.warmup() >= 120, name


if __name__ == "__main__":
    tests = [
        test_all_versions_registered,
        test_backtest_runs_and_is_spot_consistent,
        test_every_registered_strategy_runs_and_is_spot,
        test_reset_makes_reuse_deterministic,
        test_no_lookahead_before_training_cut,
        test_hold_forever_returns_zero,
        test_analysis_grid_search_and_walk_forward,
        test_buy_hold_captures_full_market,
        test_initial_equity_is_normalized,
        test_regime_warmup_covers_atr_period,
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print("\nALL SMOKE TESTS PASSED")
