"""Walk-forward (out-of-sample) evaluation.

Slide a fixed train window; on each step pick the best params by grid search on
the train slice, then apply them to the following, unseen test slice. Params are
always chosen on strictly earlier bars (no look-ahead).

Each test window is evaluated with its indicators warmed by that window's train
history but **starting flat** (``run_backtest(..., warmup=test_start)``), so a
window never inherits a position built during training. The per-window target
ratios are assembled onto a single contiguous out-of-sample timeline and run
through one equity pass (``simulate_targets``): returns compound continuously
across window boundaries, any position left open at a boundary is flattened (with
its cost) when the next window starts flat, and buy & hold is charged a single
initial buy for the whole OOS period.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from evergreen_lab.analysis.optimizer import ParamGrid, StrategySpec, _make, grid_search
from evergreen_lab.core.candle import Candle
from evergreen_lab.engine import Cost
from evergreen_lab.engine.backtest import run_backtest, simulate_targets
from evergreen_lab.engine.metrics import Summary, summarize
from evergreen_lab.engine.result import BacktestRow


@dataclass(frozen=True)
class WalkForwardWindow:
    train_from: datetime
    train_to: datetime
    test_from: datetime
    test_to: datetime
    params: dict[str, Any]


@dataclass(frozen=True)
class WalkForwardResult:
    rows: tuple[BacktestRow, ...]
    summary: Summary
    windows: list[WalkForwardWindow]


def walk_forward(
    strategy: StrategySpec,
    candles: Sequence[Candle],
    param_grid: ParamGrid = None,
    *,
    train_size: int,
    test_size: int,
    cost: Cost | None = None,
    metric: str = "calmar",
) -> WalkForwardResult:
    if train_size <= 1 or test_size <= 0:
        raise ValueError("train_size must be > 1 and test_size > 0")
    cost = cost or Cost()
    n = len(candles)
    if n < train_size + test_size:
        raise ValueError(f"need at least train_size+test_size ({train_size + test_size}) candles, got {n}")

    targets: list[float | None] = [None] * n
    windows: list[WalkForwardWindow] = []
    last_end = train_size

    a = 0
    while a + train_size + test_size <= n:
        b = a + train_size
        end = min(b + test_size, n)
        best = grid_search(strategy, candles[a:b], param_grid, cost=cost, metric=metric, top_k=1)[0].params

        # One extra bar so the last test bar is fillable (not the slice's final bar).
        end_slice = min(end + 1, n)
        run = run_backtest(_make(strategy, best), candles[a:end_slice], cost, warmup=b - a)
        for g in range(b, end):
            targets[g] = run.rows[g - a].target_ratio

        windows.append(
            WalkForwardWindow(
                train_from=candles[a].timestamp, train_to=candles[b - 1].timestamp,
                test_from=candles[b].timestamp, test_to=candles[end - 1].timestamp, params=best,
            )
        )
        last_end = end
        a += test_size

    oos_candles = list(candles[train_size:last_end])
    oos_targets = [targets[train_size + i] or 0.0 for i in range(len(oos_candles))]
    if len(oos_candles) < 2:
        raise ValueError("walk-forward produced too few out-of-sample bars")

    rows = simulate_targets(oos_candles, oos_targets, cost)
    return WalkForwardResult(rows=rows, summary=summarize(rows), windows=windows)
