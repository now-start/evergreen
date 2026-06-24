from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from evergreen_backtest.backtest import BacktestEvaluator, BacktestRow, BacktestSummary
from evergreen_backtest.data import CandleBar
from evergreen_backtest.modeling import ModelSignal
from evergreen_backtest.optimizer import CandidateResult, HyperparameterOptimizer
from evergreen_backtest.versions import VersionAdapter, snake_to_camel, summary_to_dict, to_plain_dict


@dataclass(frozen=True)
class WalkForwardConfig:
    enabled: bool = True
    train_window_days: int = 1095
    test_window_days: int = 180
    min_train_bars: int = 365

    def __post_init__(self) -> None:
        if self.train_window_days <= 0:
            raise ValueError("train_window_days must be > 0")
        if self.test_window_days <= 0:
            raise ValueError("test_window_days must be > 0")
        if self.min_train_bars < 2:
            raise ValueError("min_train_bars must be >= 2")


@dataclass(frozen=True)
class WalkForwardWindow:
    train_from: datetime
    train_to: datetime
    test_from: datetime
    test_to: datetime
    selected_version: str
    selected_params: Any
    train_summary: BacktestSummary
    test_summary: BacktestSummary


@dataclass(frozen=True)
class WalkForwardResult:
    rows: list[BacktestRow]
    summary: BacktestSummary
    windows: list[WalkForwardWindow]

    @property
    def current_window(self) -> WalkForwardWindow:
        if not self.windows:
            raise ValueError("walk-forward result has no windows")
        return self.windows[-1]

    def summary_row(self) -> dict[str, Any]:
        row = summary_to_dict(self.summary)
        row["version"] = self.current_window.selected_version
        row["phase"] = "walk_forward"
        row["window_count"] = len(self.windows)
        return row

    def contract(self, adapter_by_version: dict[str, VersionAdapter]) -> dict[str, Any]:
        current = self.current_window
        adapter = adapter_by_version[current.selected_version]
        params = to_plain_dict(current.selected_params)
        params_camel = {snake_to_camel(key): value for key, value in params.items()}
        java_params = {key: value for key, value in params_camel.items() if key in adapter.java_param_fields}
        return {
            "selectedVersion": current.selected_version,
            "trainedThrough": current.train_to.isoformat(),
            "effectiveFrom": current.test_from.isoformat(),
            "effectiveTo": current.test_to.isoformat(),
            "javaInteropReady": True,
            "javaParamsCamelCase": java_params,
            "selectedParams": params,
            "selectedParamsCamelCase": params_camel,
            "summary": summary_to_dict(self.summary),
            "windows": [
                {
                    "trainFrom": window.train_from.isoformat(),
                    "trainTo": window.train_to.isoformat(),
                    "testFrom": window.test_from.isoformat(),
                    "testTo": window.test_to.isoformat(),
                    "selectedVersion": window.selected_version,
                    "selectedParamsCamelCase": {
                        snake_to_camel(key): value for key, value in to_plain_dict(window.selected_params).items()
                    },
                    "train": summary_to_dict(window.train_summary),
                    "test": summary_to_dict(window.test_summary),
                }
                for window in self.windows
            ],
        }


class WalkForwardSelector:
    def __init__(self, optimizer: HyperparameterOptimizer | None = None) -> None:
        self.optimizer = optimizer or HyperparameterOptimizer()

    def run(
            self,
            *,
            bars: list[CandleBar],
            adapters: list[VersionAdapter],
            configs: dict[str, Any],
            config: WalkForwardConfig,
    ) -> WalkForwardResult:
        if len(bars) < config.min_train_bars + 2:
            raise ValueError("not enough bars for walk-forward selection")

        windows: list[WalkForwardWindow] = []
        cursor = _first_test_start_index(bars, config)
        walk_start = cursor
        walk_signals: list[ModelSignal] = []
        walk_cost_units: list[float] = []
        walk_end = cursor

        while cursor < len(bars) - 1:
            train_start = _train_start_index(bars, cursor, config)
            train_bars = bars[train_start:cursor]
            test_end = _test_end_index(bars, cursor, config)
            test_bars = bars[cursor:test_end]
            if len(train_bars) < config.min_train_bars or len(test_bars) < 2:
                break

            adapter, candidate = self._select_best_candidate(adapters, configs, train_bars)
            model = adapter.model()
            signals = model.evaluate(bars[:test_end], candidate.params)
            walk_signals.extend(signals[cursor:test_end])
            walk_cost_units.extend(
                [candidate.params.fee_per_side + candidate.params.slippage]
                * len(signals[cursor:test_end])
            )
            walk_end = test_end

            test_result = BacktestEvaluator().evaluate(
                test_bars,
                signals[cursor:test_end],
                fee_per_side=candidate.params.fee_per_side,
                slippage=candidate.params.slippage,
            )

            windows.append(
                WalkForwardWindow(
                    train_from=train_bars[0].timestamp,
                    train_to=train_bars[-1].timestamp,
                    test_from=test_bars[0].timestamp,
                    test_to=test_bars[-1].timestamp,
                    selected_version=adapter.name,
                    selected_params=candidate.params,
                    train_summary=candidate.result.summary,
                    test_summary=test_result.summary,
                )
            )
            cursor = test_end

        if not walk_signals or not windows:
            raise RuntimeError("walk-forward selection produced no windows")
        walk_bars = bars[walk_start:walk_end]
        result = BacktestEvaluator().evaluate(
            walk_bars,
            walk_signals,
            fee_per_side=walk_cost_units[0],
            slippage=0.0,
            cost_units=walk_cost_units,
        )
        return WalkForwardResult(rows=result.rows, summary=result.summary, windows=windows)

    def _select_best_candidate(
            self,
            adapters: list[VersionAdapter],
            configs: dict[str, Any],
            train_bars: list[CandleBar],
    ) -> tuple[VersionAdapter, CandidateResult]:
        best: tuple[VersionAdapter, CandidateResult] | None = None
        for adapter in adapters:
            config = configs[adapter.name]
            if not config.enabled:
                continue
            candidates = self.optimizer.search(
                model=adapter.model(),
                bars=train_bars,
                params_grid=adapter.iter_parameter_grid(config),
                top_k=1,
                parallelism=getattr(config, "grid_parallelism", 1),
            )
            candidate = candidates[0]
            if best is None or _candidate_rank(candidate) > _candidate_rank(best[1]):
                best = (adapter, candidate)
        if best is None:
            raise RuntimeError("no enabled model for walk-forward selection")
        return best


def _first_test_start_index(bars: list[CandleBar], config: WalkForwardConfig) -> int:
    start_time = bars[0].timestamp
    for index, bar in enumerate(bars):
        if index >= config.min_train_bars and (bar.timestamp - start_time).days >= config.train_window_days:
            return index
    return config.min_train_bars


def _train_start_index(bars: list[CandleBar], cursor: int, config: WalkForwardConfig) -> int:
    train_end_time = bars[cursor - 1].timestamp
    for index in range(cursor):
        if (train_end_time - bars[index].timestamp).days <= config.train_window_days:
            return index
    return 0


def _test_end_index(bars: list[CandleBar], cursor: int, config: WalkForwardConfig) -> int:
    test_start_time = bars[cursor].timestamp
    index = cursor + 1
    while index < len(bars) and (bars[index].timestamp - test_start_time).days < config.test_window_days:
        index += 1
    return min(len(bars), max(cursor + 2, index))


def _candidate_rank(candidate: CandidateResult) -> tuple[float, float, float]:
    return (_rank_value(candidate.calmar_like), _rank_value(candidate.cagr), _rank_value(candidate.final_equity))


def _rank_value(value: float) -> float:
    return value if isinstance(value, (int, float)) and value == value else float("-inf")
