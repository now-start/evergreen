"""Walk-forward (아웃오브샘플) 평가.

고정된 학습(train) 구간을 슬라이드한다; 각 단계마다 train 구간에 대한 그리드
서치로 최적 파라미터를 고르고, 그 파라미터를 다음의 아직 보지 않은 test 구간에
적용한다. 파라미터는 항상 엄격히 이전 바들로만 선택한다(미리보기 없음).

각 test 구간은 그 구간의 train 히스토리로 지표를 워밍업하되 **flat 상태로
시작**해서 평가한다(``run_backtest(..., warmup=test_start)``). 구간별 목표
비율들은 하나의 연속된 아웃오브샘플 타임라인으로 이어붙여져 한 번의 equity
계산(``simulate_targets``)을 통과한다.

정확성과 관련된 세 가지 세부사항:
* 포지션은 **매 구간 경계에서 flat으로 강제**된다 — 각 구간의 목표값은 flat
  상태에서 만들어졌으므로, 포지션이 경계를 넘어 이어지면 안 된다(그러면
  turnover가 실제보다 적게 계산되거나 일관되지 않은 포지션을 이어받게 된다).
* train 구간 앞부분에 스스로 맞추는(fit) 전략(예: v6의 ``train_fraction``을
  통한 scale)은 **이번 구간의 train 경계에 맞춰진다**, 그래서 그 fit이 test
  캔들을 절대 보지 않는다. ``train_fraction`` 파라미터가 없는 전략에는 영향 없음.
* **마지막 OOS 바는 그대로 이어간다**(체결할 다음 open이 없음). 이는
  ``run_backtest``가 마지막 바를 강제로 HOLD하는 것과 같은 맥락이라, 가상의
  체결이 기록되지 않는다.
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
    boundary_starts: list[int] = []  # 첫 구간 이후 각 구간의 전역 test 시작 인덱스
    last_end = train_size

    a = 0
    first = True
    while a + train_size + test_size <= n:
        b = a + train_size
        end = min(b + test_size, n)
        best = grid_search(strategy, candles[a:b], param_grid, cost=cost, metric=metric, top_k=1)[0].params

        # 마지막 test 바의 결정도 체결 가능하도록 한 바를 더 포함(슬라이스의 마지막 바가 아니게).
        end_slice = min(end + 1, n)
        # 스스로 맞추는(self-fitting) train 구간(v6의 scale)을 이번 구간의 train 경계에
        # 맞춰서 fit이 test 캔들을 절대 보지 않게 한다; 해당 kwarg가 없는 전략은 정상적으로 대체됨.
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

    # 매 경계에서 flat으로: 이전 구간의 마지막 바를 강제로 0.0으로 만들어서 다음
    # (flat 시작) 구간이 경계를 넘어 포지션을 이어받지 않게 한다.
    for b in boundary_starts:
        if targets[b - 1] is not None:
            targets[b - 1] = 0.0

    oos_candles = list(candles[train_size:last_end])
    if len(oos_candles) < 2:
        raise ValueError("walk-forward produced too few out-of-sample bars")
    oos_targets = [targets[train_size + i] or 0.0 for i in range(len(oos_candles))]
    # 마지막 OOS 바는 체결할 다음 open이 없다 — 그대로 이어간다(가상의 거래 없음).
    oos_targets[-1] = oos_targets[-2]

    rows = simulate_targets(oos_candles, oos_targets, cost)
    return WalkForwardResult(rows=rows, summary=summarize(rows), windows=windows)
