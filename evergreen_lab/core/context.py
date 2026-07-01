from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from evergreen_lab.core.candle import Candle
from evergreen_lab.core.position import Position


@dataclass(frozen=True)
class BarContext:
    """전략이 현재 바에서 읽을 수 있는 모든 것. 계약상 인과적(causal)이다:
    ``index``까지의(포함) 정보만 읽어야 하며(미래 미리보기 없음) — ``candles``를
    ``index`` 이후로 인덱싱해서는 안 된다.

    ``features``는 엔진이 ``Strategy.features``를 통해 한 번 미리 계산해둔
    지표 시리즈를 담고 있다; ``feature(name)``은 현재 바의 값을,
    ``feature(name, lag=k)``는 ``k``바 이전 값을 반환한다.
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
        """현재 바까지의(포함) 종가들 (인과적 슬라이스)."""
        return [candle.close for candle in self.candles[: self.index + 1]]
