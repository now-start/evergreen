"""Candle loading — Upbit fetch + CSV cache, returning core ``Candle`` objects.

Self-contained: the fetcher lives in ``evergreen_lab/upbit_data.py``. The
upbit-sdk import there is lazy, so ``import evergreen_lab`` needs no network or SDK.
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
    from evergreen_lab.upbit_data import load_bars  # lazy: no import-time network/SDK dep

    return load_bars(
        market=market, from_dt=from_dt, to_dt=to_dt, cache_dir=cache_dir, interval_key=interval, client=client
    )
