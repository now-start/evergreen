"""Moving-average trend signals from closed prices."""

from collections.abc import Sequence
from decimal import Decimal

from evergreen.strategies.types import Side

ZERO = Decimal(0)


def sma_target(
    closes: Sequence[Decimal], holding: bool, *, fast_period: int = 20, slow_period: int = 60
) -> Side | None:
    if len(closes) < slow_period:
        return None
    fast = sum(closes[-fast_period:], ZERO) / fast_period
    slow = sum(closes[-slow_period:], ZERO) / slow_period
    if holding:
        return "sell" if fast < slow else None
    return "buy" if fast > slow * Decimal("1.002") else None
