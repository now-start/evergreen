from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cost:
    """Per-side trading cost (fee + slippage). A market property, not a strategy one."""

    fee_per_side: float = 0.0005
    slippage: float = 0.0002

    def __post_init__(self) -> None:
        if self.fee_per_side < 0.0 or self.slippage < 0.0:
            raise ValueError("fee_per_side and slippage must be >= 0")

    @property
    def per_side(self) -> float:
        return self.fee_per_side + self.slippage
