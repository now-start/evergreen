"""Candle loading. Reuses the proven Upbit fetch + CSV cache in
``evergreen_research.data`` (the one piece worth keeping from the old package);
the import is lazy so ``import evergreen_lab`` never needs the network or the SDK.
"""

from __future__ import annotations

from datetime import datetime

from evergreen_lab.core.candle import Candle


def load_candles(
    *,
    market: str = "KRW-BTC",
    from_dt: datetime,
    to_dt: datetime | None = None,
    interval: str = "minute_240",
    cache_dir: str = "outputs/data/upbit-cache",
    client=None,
) -> list[Candle]:
    from evergreen_research.data import load_bars  # lazy: no import-time network dep

    bars = load_bars(
        market=market,
        from_dt=from_dt,
        to_dt=to_dt,
        cache_dir=cache_dir,
        interval_key=interval,
        client=client,
    )
    return [Candle(b.timestamp, b.open, b.high, b.low, b.close, b.volume) for b in bars]
