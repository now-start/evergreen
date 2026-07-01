from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from evergreen_lab.core.candle import Candle
from evergreen_lab.core.position import Position


@dataclass(frozen=True)
class BarContext:
    """Everything a strategy may read *at* the current bar. Causal by contract:
    read only information up to and including ``index`` (no look-ahead) — do NOT
    index ``candles`` past ``index``.

    ``features`` holds indicator series precomputed once by the engine via
    ``Strategy.features``; ``feature(name)`` returns that series at the current
    bar and ``feature(name, lag=k)`` returns it ``k`` bars back.
    """

    candles: Sequence[Candle]
    index: int
    position: Position
    features: Mapping[str, Sequence[float]] = field(default_factory=dict)

    @property
    def now(self) -> Candle:
        return self.candles[self.index]

    @property
    def in_position(self) -> bool:
        return self.position.in_position

    def feature(self, name: str, lag: int = 0) -> float:
        series = self.features.get(name)
        if series is None:
            return math.nan
        j = self.index - lag
        if j < 0 or j >= len(series):
            return math.nan
        return series[j]

    def closes(self) -> list[float]:
        """Closing prices up to and including the current bar (causal slice)."""
        return [candle.close for candle in self.candles[: self.index + 1]]
