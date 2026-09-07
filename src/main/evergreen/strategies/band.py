"""Prior-window population-standard-deviation mean reversion."""

from collections.abc import Sequence
from decimal import Decimal

from evergreen.market import Candle
from evergreen.strategies.types import Side

ZERO = Decimal(0)


def target(history: Sequence[Candle], holding: bool) -> Side | None:
    if len(history) < 25:
        return None
    previous = [bar.close for bar in history[-25:-1]]
    mean = sum(previous, ZERO) / 24
    if holding:
        return "sell" if history[-1].close >= mean else None
    variance = sum(((price - mean) ** 2 for price in previous), ZERO) / 24
    return "buy" if variance > 0 and history[-1].close < mean - 2 * variance.sqrt() else None
