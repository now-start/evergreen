"""evergreen_lab — a minimal, generic spot backtest/evaluation framework.

Design goals
------------
* Add a strategy by dropping one file in ``strategies/`` — no framework edits.
* Evaluate it in a notebook in three lines: pick, run, look at profit.
* One strategy interface, per-bar and position-aware, shaped like the live Java
  engine so a validated strategy migrates by hand 1:1 (no JSON "contract", no
  weight export, no parity harness).

Layers
------
* ``core``       — Candle, Action, Position, BarContext, Strategy, registry.
* ``indicators`` — causal technical indicators (no look-ahead).
* ``engine``     — spot backtest engine (open->open fills), cost model, metrics.
* ``data``       — candle loading (reuses the proven Upbit fetch + CSV cache).
* ``strategies`` — one file per strategy; auto-registered on import.
* ``api``        — ``evaluate(...)`` notebook entry point.
"""

from __future__ import annotations

from evergreen_lab.core import Action, BarContext, Candle, Position, Strategy
from evergreen_lab.core.registry import create, list_strategies, register
from evergreen_lab.engine import BacktestResult, BacktestRow, Cost, Summary, run_backtest
from evergreen_lab.api import evaluate, evaluate_candles

# Importing the strategies package populates the registry as a side effect.
from evergreen_lab import strategies as strategies  # noqa: F401,E402

__all__ = [
    "Action", "BarContext", "BacktestResult", "BacktestRow", "Candle", "Cost",
    "Position", "Strategy", "Summary", "create", "evaluate", "evaluate_candles",
    "list_strategies", "register", "run_backtest",
]
