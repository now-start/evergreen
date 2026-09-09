"""Shared causal price features for offline breakout experiments."""

from collections.abc import Sequence
from decimal import Decimal

from evergreen.market import Candle


def prior_mean_true_range(history: Sequence[Candle], *, lookback: int = 24) -> Decimal:
    """Mean of the prior ranges, excluding the final (signal) bar."""
    if lookback not in (24, 168):
        raise ValueError("연구용 평균 TR은 24/168봉만 지원합니다")
    if len(history) < lookback + 2:
        raise ValueError(
            f"신호 봉·이전{lookback}봉·이전종가를 위해 최소{lookback + 2}봉이 필요합니다"
        )
    return (
        sum(
            (
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous.close),
                    abs(bar.low - previous.close),
                )
                for previous, bar in zip(
                    history[-lookback - 2 : -2], history[-lookback - 1 : -1], strict=True
                )
            ),
            Decimal(0),
        )
        / lookback
    )
