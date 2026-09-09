"""Research-only regime routing; exits retain entry ownership unless bearish or risk-stopped."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from evergreen.market import Candle
from evergreen.strategies import band, breakout
from evergreen.strategies.types import Side

# -1 unknown; 0 down; 1 sideways; 2 up. Model class order is part of the protocol.
REGIME_STRATEGIES = ("regime-rules-v1", "regime-mlp-v1")
RegimePolicy = Literal["routing", "sideways-cash", "breakout-filter"]


def rule_regime(history: Sequence[Candle]) -> int:
    if len(history) < 168:
        return -1
    closes = [bar.close for bar in history[-168:]]
    ratio = (sum(closes[-48:], Decimal(0)) / 48) / (sum(closes, Decimal(0)) / 168)
    return 2 if ratio > Decimal("1.01") else 0 if ratio < Decimal(".99") else 1


def stabilize(values: dict[datetime, int]) -> dict[datetime, int]:
    """Causal 3-bar confirmation and 6-hour residence, reset across missing hours."""
    result: dict[datetime, int] = {}
    active, candidate, count = -1, -1, 0
    changed: datetime | None = None
    previous: datetime | None = None
    for time, value in sorted(values.items()):
        if value not in (-1, 0, 1, 2):
            raise ValueError("알 수 없는 장세 코드")
        if time.utcoffset() is None:
            raise ValueError("장세 시각에는 시간대가 필요합니다")
        if previous is not None and time - previous != timedelta(hours=1):
            active, candidate, count, changed = -1, -1, 0, None
        count = count + 1 if value == candidate else 1
        candidate = value
        if (
            count >= 3
            and value != active
            and (changed is None or time - changed >= timedelta(hours=6))
        ):
            active, changed = value, time
        # An uncertain raw prediction blocks new entries immediately (exits retain ownership).
        result[time] = -1 if value == -1 else active
        previous = time
    return result


class RegimeRouter:
    def __init__(self, policy: RegimePolicy = "routing") -> None:
        if policy not in ("routing", "sideways-cash", "breakout-filter"):
            raise ValueError("알 수 없는 장세 정책")
        self.policy = policy
        self.owner: int | None = None

    def target(self, history: Sequence[Candle], holding: bool, regime: int) -> Side | None:
        if holding:
            if self.policy == "breakout-filter":
                return breakout.target(history, True)
            if regime == 0:
                return "sell"
            if self.owner == 2:
                return breakout.target(history, True)
            if self.owner == 1:
                return band.target(history, True)
            raise ValueError("보유 포지션의 진입 전략이 없습니다")
        self.owner = None
        signal = (
            breakout.target(history, False)
            if regime == 2
            else band.target(history, False)
            if regime == 1 and self.policy == "routing"
            else None
        )
        if signal == "buy":
            self.owner = regime
        return signal
