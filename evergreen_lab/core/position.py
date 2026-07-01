from __future__ import annotations

from dataclasses import dataclass

POSITION_EPSILON = 1e-12


@dataclass(frozen=True)
class Position:
    """현재 바 동안 보유 중인 포지션 (스팟: 비율은 [0, 1] 범위)."""

    ratio: float = 0.0
    entry_price: float = 0.0

    @property
    def in_position(self) -> bool:
        return self.ratio > POSITION_EPSILON

    @property
    def is_full(self) -> bool:
        return self.ratio >= 1.0 - POSITION_EPSILON
