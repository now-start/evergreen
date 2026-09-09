"""Shared causal price features for offline breakout experiments."""

from collections.abc import Sequence
from decimal import Decimal

from evergreen.market import Candle


def prior_mean_true_range(history: Sequence[Candle]) -> Decimal:
    """Mean of the prior24 ranges, excluding the final (signal) bar."""
    if len(history) < 26:
        raise ValueError("신호 봉·이전24봉·이전종가를 위해 최소26봉이 필요합니다")
    return (
        sum(
            (
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous.close),
                    abs(bar.low - previous.close),
                )
                for previous, bar in zip(history[-26:-2], history[-25:-1], strict=True)
            ),
            Decimal(0),
        )
        / 24
    )
