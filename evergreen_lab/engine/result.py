from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from evergreen_lab.engine.metrics import Summary


@dataclass(frozen=True)
class BacktestRow:
    timestamp: datetime
    close: float
    action: str
    target_ratio: float
    pos_open: float
    ret_oo: float
    trade: float
    equity: float
    equity_bh: float


@dataclass(frozen=True)
class BacktestResult:
    rows: tuple[BacktestRow, ...]
    summary: Summary

    def to_frame(self):
        import pandas as pd

        return pd.DataFrame([vars(row) for row in self.rows])

    def summary_frame(self):
        import pandas as pd

        return pd.DataFrame([vars(self.summary)])

    def plot_equity(self, ax=None):
        import matplotlib.pyplot as plt

        timestamps = [row.timestamp for row in self.rows]
        if ax is None:
            _, ax = plt.subplots(figsize=(11, 4))
        ax.plot(timestamps, [row.equity for row in self.rows], label="strategy")
        ax.plot(timestamps, [row.equity_bh for row in self.rows], label="buy & hold", alpha=0.6)
        ax.set_title("Equity curve (start = 1.0)")
        ax.set_ylabel("equity")
        ax.legend()
        ax.grid(True, alpha=0.3)
        return ax

    def describe(self) -> str:
        s = self.summary
        open_note = "  [position still open at end]" if s.open_position else ""
        return (
            f"total_return={s.total_return:+.2%}  buy_hold={s.buy_hold_return:+.2%}  "
            f"cagr={s.cagr:+.2%}  mdd={s.mdd:.2%}  round_trips={s.round_trips}  "
            f"win_rate={s.win_rate:.1%}  orders={s.orders}  bars={s.bars}{open_note}\n{s.period}"
        )

    def __repr__(self) -> str:
        return f"BacktestResult(\n  {self.describe()}\n)"
