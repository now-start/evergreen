from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any

from evergreen_research.data import CandleBar
from evergreen_research.modeling import ModelSignal


MIN_EQUITY = 1e-12


@dataclass(frozen=True)
class BacktestRow:
    timestamp: datetime
    open: float
    close: float
    action: str
    signal_reason: str
    target_position_ratio: float
    pos_open: float
    ret_oo: float
    equity: float
    equity_bh: float
    trade: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestSummary:
    final_equity: float
    final_equity_bh: float
    cagr: float
    mdd: float
    trades: int
    range: str


@dataclass(frozen=True)
class BacktestResult:
    rows: list[BacktestRow]
    summary: BacktestSummary


class BacktestEvaluator:
    def evaluate(
            self,
            bars: list[CandleBar],
            signals: list[ModelSignal],
            *,
            fee_per_side: float,
            slippage: float,
            cost_units: list[float] | None = None,
            initial_equity: float = 1.0,
            initial_equity_bh: float | None = None,
    ) -> BacktestResult:
        if len(bars) != len(signals):
            raise ValueError("bars and signals must have the same length")
        if len(bars) < 2:
            raise ValueError("At least 2 bars are required")
        if fee_per_side < 0.0 or slippage < 0.0:
            raise ValueError("fee_per_side and slippage must be >= 0")
        if cost_units is not None and len(cost_units) != len(bars):
            raise ValueError("cost_units must have the same length as bars")

        n = len(bars)
        cost_unit = fee_per_side + slippage
        close_target = [_finite_or_zero(signal.target_position_ratio) for signal in signals]
        pos_open = [0.0] * n
        ret_oo = [0.0] * n
        trade = [0.0] * n
        equity = [0.0] * n
        equity_bh = [0.0] * n

        for i in range(n - 1):
            ret_oo[i] = (bars[i + 1].open / bars[i].open) - 1.0

        for i in range(n):
            pos_open[i] = 0.0 if i == 0 else close_target[i - 1]
            trade[i] = abs(pos_open[i]) if i == 0 else abs(pos_open[i] - pos_open[i - 1])

        equity[0] = max(MIN_EQUITY, initial_equity)
        equity_bh[0] = max(MIN_EQUITY, initial_equity_bh if initial_equity_bh is not None else initial_equity * (1.0 - cost_unit))

        for i in range(1, n):
            row_cost_unit = cost_units[i - 1] if cost_units is not None else cost_unit
            turnover_cost = trade[i] * row_cost_unit
            gross = 1.0 + (pos_open[i] * ret_oo[i]) - turnover_cost
            equity[i] = MIN_EQUITY if (not math.isfinite(gross) or gross <= 0.0) else max(MIN_EQUITY, equity[i - 1] * gross)

            bh_gross = 1.0 + ret_oo[i]
            equity_bh[i] = MIN_EQUITY if (not math.isfinite(bh_gross) or bh_gross <= 0.0) else max(MIN_EQUITY, equity_bh[i - 1] * bh_gross)

        rows = [
            BacktestRow(
                timestamp=bars[i].timestamp,
                open=bars[i].open,
                close=bars[i].close,
                action=signals[i].action.value,
                signal_reason=signals[i].signal_reason,
                target_position_ratio=close_target[i],
                pos_open=pos_open[i],
                ret_oo=ret_oo[i],
                equity=equity[i],
                equity_bh=equity_bh[i],
                trade=trade[i],
                diagnostics=signals[i].diagnostics,
            )
            for i in range(n)
        ]
        return BacktestResult(rows=rows, summary=summarize_rows(rows))


def summarize_rows(rows: list[BacktestRow]) -> BacktestSummary:
    if not rows:
        raise ValueError("rows must not be empty")
    final_equity = rows[-1].equity
    final_equity_bh = rows[-1].equity_bh
    years = max(1.0 / 365.25, ((rows[-1].timestamp - rows[0].timestamp).days / 365.25))
    cagr = math.pow(final_equity / rows[0].equity, 1.0 / years) - 1.0 if rows[0].equity > 0.0 else math.nan
    mdd = max_drawdown([row.equity for row in rows])
    trades = sum(1 for row in rows if row.trade > 1e-12)
    return BacktestSummary(
        final_equity=final_equity,
        final_equity_bh=final_equity_bh,
        cagr=cagr,
        mdd=mdd,
        trades=trades,
        range=f"{rows[0].timestamp} -> {rows[-1].timestamp}",
    )


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    mdd = 0.0
    for value in equity:
        peak = max(peak, value)
        mdd = min(mdd, (value / peak) - 1.0)
    return mdd


def _finite_or_zero(value: float) -> float:
    return value if math.isfinite(value) else 0.0
