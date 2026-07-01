from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    """OHLCV 바 하나. 불변(immutable)이며, 실거래 Java 엔진이 소비하는 것과 동일한 형태."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
