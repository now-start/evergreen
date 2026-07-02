"""전략 하이퍼파라미터에 대한 그리드 서치, 백테스트 지표로 순위를 매긴다."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evergreen_lab.core.candle import Candle
from evergreen_lab.core.registry import create
from evergreen_lab.core.strategy import Strategy
from evergreen_lab.engine import BacktestResult, Cost, run_backtest


@dataclass(frozen=True)
class Candidate:
    params: dict[str, Any]
    result: BacktestResult
    score: float


ParamGrid = dict[str, Sequence[Any]] | Sequence[dict[str, Any]] | None
StrategySpec = str | Callable[..., Strategy]


def expand_grid(param_grid: ParamGrid) -> list[dict[str, Any]]:
    if not param_grid:
        return [{}]
    if isinstance(param_grid, dict):
        keys = list(param_grid.keys())
        return [dict(zip(keys, combo)) for combo in itertools.product(*(param_grid[k] for k in keys))]
    return [dict(params) for params in param_grid]


def score_summary(summary, metric: str) -> float:
    if metric == "total_return":
        value = summary.total_return
    elif metric == "final_equity":
        value = summary.final_equity
    elif metric == "calmar":
        value = summary.cagr / abs(summary.mdd) if summary.mdd != 0.0 else (summary.cagr if summary.cagr > 0 else 0.0)
    else:
        raise ValueError(f"unknown metric: {metric!r} (use calmar | total_return | final_equity)")
    return value if math.isfinite(value) else float("-inf")


def _make(spec: StrategySpec, params: dict[str, Any]) -> Strategy:
    return create(spec, **params) if isinstance(spec, str) else spec(**params)


def _maybe_progress(iterable, total: int, desc: str, progress: bool):
    """진행 바(tqdm.auto — 노트북/터미널 공용)로 감싼다. tqdm 없거나 progress=False면 그대로 반환한다.
    조합마다 백테스트(+v6는 MLP 학습)가 돌아 오래 걸리므로 HPO 진행 상황을 보여주는 게 유용하다."""
    if not progress:
        return iterable
    try:
        from tqdm.auto import tqdm
    except ImportError:
        return iterable
    return tqdm(iterable, total=total, desc=desc, unit="combo")


def grid_search(
    strategy: StrategySpec,
    candles: Sequence[Candle],
    param_grid: ParamGrid = None,
    *,
    cost: Cost | None = None,
    metric: str = "calmar",
    top_k: int = 5,
    progress: bool = False,
) -> list[Candidate]:
    """모든 파라미터 조합을 백테스트하고 ``metric`` 기준 상위 ``top_k``를 반환한다.
    ``progress=True``면 조합 진행 바를 표시한다(조합마다 학습/백테스트가 돌아 오래 걸릴 때 유용)."""
    if top_k <= 0:
        raise ValueError("top_k must be > 0")
    cost = cost or Cost()
    combos = expand_grid(param_grid)
    candidates: list[Candidate] = []
    for params in _maybe_progress(combos, len(combos), "HPO grid", progress):
        result = run_backtest(_make(strategy, params), candles, cost)
        candidates.append(Candidate(params=params, result=result, score=score_summary(result.summary, metric)))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]
