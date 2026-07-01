"""Walk-forward (out-of-sample) evaluation.

Slide a fixed train window; on each step pick the best params by grid search on
the train slice, then apply them to the following, unseen test slice. Params are
always chosen on strictly earlier bars (no look-ahead).

Each test window is evaluated with its indicators warmed by that window's train
history but **starting flat** (``run_backtest(..., warmup=test_start)``). The
per-window target ratios are assembled onto a single contiguous out-of-sample
timeline and run through one equity pass (``simulate_targets``).

Three correctness details:
* The position is **flattened at every window boundary** — each window's targets
  were produced from a flat start, so a position must not bleed across the seam
  (that would under-charge turnover / inherit an inconsistent position).
* A strategy that fits on a train prefix (e.g. v6's scale via ``train_fraction``)
  is **aligned to this window's train boundary**, so its fit never sees test
  candles. Harmless for strategies without a ``train_fraction`` parameter.
* The **final OOS bar is carried** (no next open exists to fill it), mirroring
  ``run_backtest``'s forced last-bar HOLD, so no phantom fill is recorded.
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
    boundary_starts: list[int] = []  # global test-start index of each window after the first
    last_end = train_size

    a = 0
    first = True
    while a + train_size + test_size <= n:
        b = a + train_size
        end = min(b + test_size, n)
        best = grid_search(strategy, candles[a:b], param_grid, cost=cost, metric=metric, top_k=1)[0].params

        # One extra bar so the last test-bar decision is fillable (not the slice's final bar).
        end_slice = min(end + 1, n)
        # Align a self-fitting train window (v6 scale) to THIS window's train boundary so the
        # fit never sees test candles; falls back gracefully for strategies without the kwarg.
        aligned = {**best, "train_fraction": (b - a) / (end_slice - a)}
        try:
            strat = _make(strategy, aligned)
        except TypeError:
            strat = _make(strategy, best)

        run = run_backtest(strat, candles[a:end_slice], cost, warmup=b - a)
        for g in range(b, end):
            targets[g] = run.rows[g - a].target_ratio

        if not first:
            boundary_starts.append(b)
        windows.append(
            WalkForwardWindow(
                train_from=candles[a].timestamp, train_to=candles[b - 1].timestamp,
                test_from=candles[b].timestamp, test_to=candles[end - 1].timestamp, params=best,
            )
        )
        last_end = end
        first = False
        a += test_size

    # Flatten at each boundary: force the previous window's last bar to 0.0 so the next
    # (flat-start) window does not inherit a position across the seam.
    for b in boundary_starts:
        if targets[b - 1] is not None:
            targets[b - 1] = 0.0

    oos_candles = list(candles[train_size:last_end])
    if len(oos_candles) < 2:
        raise ValueError("walk-forward produced too few out-of-sample bars")
    oos_targets = [targets[train_size + i] or 0.0 for i in range(len(oos_candles))]
    # Final OOS bar has no next open to fill on — carry it (no phantom trade).
    oos_targets[-1] = oos_targets[-2]

    rows = simulate_targets(oos_candles, oos_targets, cost)
    return WalkForwardResult(rows=rows, summary=summarize(rows), windows=windows)
