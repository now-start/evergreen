from __future__ import annotations

from dataclasses import dataclass

POSITION_EPSILON = 1e-12


@dataclass(frozen=True)
class Position:
    """The position held *during* the current bar (spot: ratio in [0, 1])."""

    ratio: float = 0.0
    entry_price: float = 0.0

    @property
    def in_position(self) -> bool:
        return self.ratio > POSITION_EPSILON

    @property
    def is_full(self) -> bool:
        return self.ratio >= 1.0 - POSITION_EPSILON
