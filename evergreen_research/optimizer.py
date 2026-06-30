from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
from typing import Any, Iterable

from evergreen_research.backtest import BacktestEvaluator, BacktestResult
from evergreen_research.data import CandleBar


@dataclass(frozen=True)
class CandidateResult:
    params: Any
    result: BacktestResult
    calmar_like: float
    cagr: float
    mdd: float
    final_equity: float


class HyperparameterOptimizer:
    def __init__(self, evaluator: BacktestEvaluator | None = None) -> None:
        self.evaluator = evaluator or BacktestEvaluator()

    def search(
            self,
            *,
            model: Any,
            bars: list[CandleBar],
            params_grid: Iterable[Any],
            top_k: int,
            parallelism: int,
    ) -> list[CandidateResult]:
        params_values = list(params_grid)
        if not params_values:
            raise ValueError("parameter grid must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        if parallelism <= 0:
            raise ValueError("parallelism must be > 0")

        def evaluate(params: Any) -> CandidateResult:
            signals = model.evaluate(bars, params)
            result = self.evaluator.evaluate(
                bars,
                signals,
                fee_per_side=params.fee_per_side,
                slippage=params.slippage,
            )
            cagr = result.summary.cagr
            mdd = result.summary.mdd
            calmar_like = math.nan if mdd == 0.0 else cagr / abs(mdd)
            return CandidateResult(
                params=params,
                result=result,
                calmar_like=calmar_like,
                cagr=cagr,
                mdd=mdd,
                final_equity=result.summary.final_equity,
            )

        if parallelism <= 1:
            candidates = [evaluate(params) for params in params_values]
        else:
            with ThreadPoolExecutor(max_workers=parallelism) as pool:
                candidates = list(pool.map(evaluate, params_values))

        candidates.sort(
            key=lambda row: (
                _rank_value(row.calmar_like),
                _rank_value(row.cagr),
                _rank_value(row.final_equity),
            ),
            reverse=True,
        )
        return candidates[:top_k]


def _rank_value(value: float) -> float:
    return value if math.isfinite(value) else float("-inf")
