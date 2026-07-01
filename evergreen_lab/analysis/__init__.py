"""Optional analysis layer on top of the core backtest: hyperparameter grid
search and walk-forward (out-of-sample) evaluation. Not needed for a plain
"run a strategy over history" evaluation — reach for it when tuning or when you
want an honest out-of-sample estimate.
"""

from __future__ import annotations

from evergreen_lab.analysis.optimizer import Candidate, grid_search
from evergreen_lab.analysis.walk_forward import WalkForwardResult, WalkForwardWindow, walk_forward

__all__ = ["Candidate", "WalkForwardResult", "WalkForwardWindow", "grid_search", "walk_forward"]
