from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Summary:
    """Headline backtest metrics: how much profit, versus buy & hold, and risk."""

    total_return: float
    buy_hold_return: float
    cagr: float
    mdd: float
    orders: int
    round_trips: int
    win_rate: float
    open_position: bool
    final_equity: float
    final_equity_bh: float
    bars: int
    period: str


def summarize(rows) -> "Summary":
    if not rows:
        raise ValueError("rows must not be empty")

    n = len(rows)
    equity = [row.equity for row in rows]
    final_equity = equity[-1]
    final_equity_bh = rows[-1].equity_bh

    # Round-trip realized return: baseline = equity at the (flat) entry-decision bar;
    # exit settles one bar later (that row carries the exit cost). A win is a
    # net-positive completed round trip. An entry with no completed exit is left open.
    round_trips = 0
    wins = 0
    entry_idx: int | None = None
    for i, row in enumerate(rows):
        if row.action == "BUY":
            entry_idx = i
        elif row.action == "SELL" and entry_idx is not None:
            exit_settle = i + 1 if i + 1 < n else i
            round_trips += 1
            if equity[exit_settle] > equity[entry_idx]:
                wins += 1
            entry_idx = None
    open_position = entry_idx is not None
    win_rate = (wins / round_trips) if round_trips > 0 else 0.0

    years = max(1.0 / 365.25, ((rows[-1].timestamp - rows[0].timestamp).days / 365.25))
    cagr = math.pow(final_equity / equity[0], 1.0 / years) - 1.0 if equity[0] > 0.0 else math.nan
    orders = sum(1 for row in rows if row.trade > 1e-12)

    return Summary(
        total_return=final_equity - 1.0,
        buy_hold_return=final_equity_bh - 1.0,
        cagr=cagr,
        mdd=_max_drawdown(equity),
        orders=orders,
        round_trips=round_trips,
        win_rate=win_rate,
        open_position=open_position,
        final_equity=final_equity,
        final_equity_bh=final_equity_bh,
        bars=n,
        period=f"{rows[0].timestamp} -> {rows[-1].timestamp}",
    )


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    mdd = 0.0
    for value in equity:
        peak = max(peak, value)
        mdd = min(mdd, (value / peak) - 1.0)
    return mdd
