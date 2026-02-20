"""Version 2 strategy logic: regime transition + ATR trailing stop."""

from dataclasses import dataclass
from enum import Enum


class MarketRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class V2Input:
    previous_regime: MarketRegime
    current_regime: MarketRegime
    atr_stop_hit: bool
    current_units: int
    max_units: int = 3


def decide_units_v2(data: V2Input) -> int:
    """Return next position units (0..max_units) for v2 strategy.

    Rules:
    - Force exit on ATR stop hit.
    - Exit on BULL -> BEAR transition.
    - Enter full units on BEAR -> BULL transition.
    - Hold otherwise.
    """
    if data.atr_stop_hit:
        return 0

    if data.previous_regime == MarketRegime.BULL and data.current_regime == MarketRegime.BEAR:
        return 0

    if data.previous_regime == MarketRegime.BEAR and data.current_regime == MarketRegime.BULL:
        return max(0, data.max_units)

    return max(0, min(data.current_units, data.max_units))


def main() -> None:
    sample = V2Input(
        previous_regime=MarketRegime.BEAR,
        current_regime=MarketRegime.BULL,
        atr_stop_hit=False,
        current_units=0,
        max_units=3,
    )
    print(f"v2 next units: {decide_units_v2(sample)}")


if __name__ == "__main__":
    main()
