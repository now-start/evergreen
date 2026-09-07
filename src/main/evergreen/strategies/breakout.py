"""168-hour breakout entry and 48-hour channel exit; no holding-time cap."""

from collections.abc import Sequence
from decimal import Decimal

from evergreen.market import Candle
from evergreen.strategies.types import Side


def target(history: Sequence[Candle], holding: bool) -> Side | None:
    if len(history) < 169:
        return None
    current = history[-1].close
    if holding:
        floor = min(bar.low for bar in history[-49:-1])
        return "sell" if current < floor else None
    ceiling = max(bar.high for bar in history[-169:-1])
    return "buy" if current > ceiling * Decimal("1.001") else None
