"""Simple RSI rebound with a long-term trend filter."""

from collections.abc import Sequence
from decimal import Decimal

from evergreen.market import Candle
from evergreen.strategies.types import Side

ZERO = Decimal(0)


def simple_rsi(closes: Sequence[Decimal]) -> Decimal:
    if len(closes) < 15:
        raise ValueError("14기간 RSI에는 최소 15개 종가가 필요합니다")
    changes = [
        current - previous for previous, current in zip(closes[-15:-1], closes[-14:], strict=True)
    ]
    gains = sum((max(change, ZERO) for change in changes), ZERO)
    losses = sum((max(-change, ZERO) for change in changes), ZERO)
    return Decimal(50) if gains + losses == ZERO else Decimal(100) * gains / (gains + losses)


def target(history: Sequence[Candle], holding: bool) -> Side | None:
    if len(history) < 168:
        return None
    closes = [bar.close for bar in history[-168:]]
    rsi = simple_rsi(closes)
    trend = sum(closes, ZERO) / 168
    if holding:
        return "sell" if rsi >= 55 or closes[-1] < trend else None
    return "buy" if simple_rsi(closes[:-1]) < 30 <= rsi and closes[-1] > trend else None
