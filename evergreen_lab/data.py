"""캔들 로딩 — Upbit fetch + CSV 캐시, 코어 ``Candle`` 객체로 반환한다.

독립적으로 동작: 실제 fetch 로직은 ``evergreen_lab/upbit_data.py``에 있다. 거기서
upbit-sdk import는 지연 import라서, ``import evergreen_lab``에는 네트워크나 SDK가
필요 없다.
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
    from evergreen_lab.upbit_data import load_bars  # 지연 import: import 시점에 네트워크/SDK 의존성을 강제하지 않기 위함

    return load_bars(
        market=market, from_dt=from_dt, to_dt=to_dt, cache_dir=cache_dir, interval_key=interval, client=client
    )
