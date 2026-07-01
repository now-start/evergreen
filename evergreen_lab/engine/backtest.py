from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab.core.action import Action
from evergreen_lab.core.candle import Candle
from evergreen_lab.core.context import BarContext
from evergreen_lab.core.position import Position
from evergreen_lab.core.strategy import Strategy
from evergreen_lab.engine.cost import Cost
from evergreen_lab.engine.metrics import summarize
from evergreen_lab.engine.result import BacktestResult, BacktestRow

MIN_EQUITY = 1e-12
EPS = 1e-12


def run_backtest(
    strategy: Strategy,
    candles: Sequence[Candle],
    cost: Cost | None = None,
    *,
    initial_equity: float = 1.0,
    warmup: int | None = None,
) -> BacktestResult:
    """Replay ``strategy`` over ``candles`` on spot and report how much it made.

    Fills happen at the next bar's open (a decision at bar i takes effect from
    open[i+1]), so there is no same-bar look-ahead. The final bar is forced to
    HOLD (nothing to fill it). Position is fed back into the strategy each bar, so
    exits only fire while holding, and a filled entry's ``entry_price`` is the
    actual fill price (next open).

    ``warmup`` optionally overrides (raises) the strategy's own warmup — used by
    walk-forward to force a flat start at a test-window boundary while still
    warming indicators with the preceding history.
    """
    if candles is None or len(candles) < 2:
        raise ValueError("at least 2 candles are required")
    cost = cost or Cost()
    n = len(candles)
    strategy.reset()  # clear per-run state so a strategy instance can be reused safely
    features = strategy.features(candles) or {}
    warmup_bars = int(strategy.warmup()) if warmup is None else max(int(strategy.warmup()), int(warmup))
    warmup_bars = max(0, warmup_bars)

    # 1) per-bar decisions -> target position ratio series (spot: 0.0 or 1.0)
    target = [0.0] * n
    action_taken = [Action.HOLD] * n
    held = 0.0
    entry_price = 0.0
    for i in range(n):
        position = Position(ratio=held, entry_price=entry_price)
        if i < warmup_bars or i >= n - 1:
            action = Action.HOLD
        else:
            action = strategy.decide(BarContext(candles, i, position, features))

        if action == Action.BUY and not position.is_full:
            new_target = 1.0
        elif action == Action.SELL and position.in_position:
            new_target = 0.0
        else:
            new_target = held

        if new_target > held + EPS:
            action_taken[i] = Action.BUY
            entry_price = candles[i + 1].open  # actual fill price; i < n-1 here
        elif new_target < held - EPS:
            action_taken[i] = Action.SELL
        else:
            action_taken[i] = Action.HOLD
        target[i] = new_target
        held = new_target

    rows = _simulate(candles, target, [a.value for a in action_taken], cost, initial_equity)
    return BacktestResult(rows=rows, summary=summarize(rows))


def simulate_targets(
    candles: Sequence[Candle],
    targets: Sequence[float],
    cost: Cost | None = None,
    *,
    initial_equity: float = 1.0,
) -> tuple[BacktestRow, ...]:
    """Equity for a pre-decided per-bar target ratio series (spot). Actions are
    derived from target transitions. Used to stitch walk-forward OOS windows into
    one continuous equity curve."""
    if len(candles) != len(targets):
        raise ValueError("candles and targets must be the same length")
    actions: list[str] = []
    held = 0.0
    for i in range(len(targets)):
        t = targets[i]
        actions.append("BUY" if t > held + EPS else ("SELL" if t < held - EPS else "HOLD"))
        held = t
    return _simulate(candles, list(targets), actions, cost or Cost(), initial_equity)


def _simulate(
    candles: Sequence[Candle],
    target: list[float],
    action_by_bar: list[str],
    cost: Cost,
    initial_equity: float,
) -> tuple[BacktestRow, ...]:
    n = len(candles)
    ret_oo = [0.0] * n
    for i in range(n - 1):
        open_i, open_j = candles[i].open, candles[i + 1].open
        ret_oo[i] = (open_j / open_i) - 1.0 if open_i > 0.0 and math.isfinite(open_i) and math.isfinite(open_j) else 0.0

    pos_open = [0.0] * n
    trade = [0.0] * n
    equity = [0.0] * n
    equity_bh = [0.0] * n
    for i in range(n):
        pos_open[i] = 0.0 if i == 0 else target[i - 1]
        trade[i] = abs(pos_open[i]) if i == 0 else abs(pos_open[i] - pos_open[i - 1])

    per_side = cost.per_side
    equity[0] = max(MIN_EQUITY, initial_equity)
    first_bh = initial_equity * (1.0 - per_side) * (1.0 + ret_oo[0])
    equity_bh[0] = MIN_EQUITY if (not math.isfinite(first_bh) or first_bh <= 0.0) else max(MIN_EQUITY, first_bh)
    for i in range(1, n):
        gross = 1.0 + (pos_open[i] * ret_oo[i]) - (trade[i] * per_side)
        equity[i] = MIN_EQUITY if (not math.isfinite(gross) or gross <= 0.0) else max(MIN_EQUITY, equity[i - 1] * gross)
        bh_gross = 1.0 + ret_oo[i]
        equity_bh[i] = MIN_EQUITY if (not math.isfinite(bh_gross) or bh_gross <= 0.0) else max(MIN_EQUITY, equity_bh[i - 1] * bh_gross)

    return tuple(
        BacktestRow(
            timestamp=candles[i].timestamp, close=candles[i].close, action=action_by_bar[i],
            target_ratio=target[i], pos_open=pos_open[i], ret_oo=ret_oo[i], trade=trade[i],
            equity=equity[i], equity_bh=equity_bh[i],
        )
        for i in range(n)
    )
