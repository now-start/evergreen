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
    """``strategy``를 ``candles``에 스팟으로 리플레이해서 얼마를 벌었는지 알려준다.

    체결은 다음 바의 open에서 일어난다(바 i에서의 결정은 open[i+1]부터 적용됨).
    그래서 같은 바 안에서 미리보기는 없다. 마지막 바는 강제로 HOLD다(체결할
    다음 바가 없음). 포지션은 매 바 전략에 다시 피드백되므로, 청산은 보유 중일
    때만 발생하고, 체결된 진입의 ``entry_price``는 실제 체결가(다음 open)다.

    ``warmup``은 선택적으로 전략 자체의 warmup을 덮어쓴다(올릴 수만 있음) —
    walk-forward가 테스트 구간 경계에서 이전 히스토리로 지표는 워밍업하면서도
    포지션은 flat 상태로 강제 시작하기 위해 사용한다.
    """
    if candles is None or len(candles) < 2:
        raise ValueError("at least 2 candles are required")
    cost = cost or Cost()
    n = len(candles)
    strategy.reset()  # 실행별 상태를 초기화해서 전략 인스턴스를 안전하게 재사용할 수 있게 함
    features = strategy.features(candles) or {}
    warmup_bars = int(strategy.warmup()) if warmup is None else max(int(strategy.warmup()), int(warmup))
    warmup_bars = max(0, warmup_bars)

    # 1) 바마다의 결정 -> 목표 포지션 비율 시리즈 (스팟: 0.0 또는 1.0)
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
            entry_price = candles[i + 1].open  # 실제 체결가; 여기서 i < n-1
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
    """이미 정해진 바별 목표 비율 시리즈(스팟)에 대한 equity를 계산한다. 액션은
    목표값 변화로부터 유도된다. walk-forward의 OOS 구간들을 하나의 연속된
    equity 곡선으로 이어붙일 때 사용한다."""
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
