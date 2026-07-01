"""노트북 진입점.

    import evergreen_lab as lab
    r = lab.evaluate("trend2", from_dt=datetime(2020, 1, 1, tzinfo=timezone.utc))
    print(r.describe()); r.plot_equity()

``evaluate``는 캔들을 로드한 뒤 백테스트하고, ``evaluate_candles``는 이미 캔들
리스트를 갖고 있을 때(테스트, 커스텀 데이터) 로딩을 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from evergreen_lab.core.candle import Candle
from evergreen_lab.core.registry import create
from evergreen_lab.core.strategy import Strategy
from evergreen_lab.data import load_candles
from evergreen_lab.engine import BacktestResult, Cost, run_backtest


def _resolve(strategy: str | Strategy, params: dict) -> Strategy:
    return strategy if isinstance(strategy, Strategy) else create(strategy, **params)


def evaluate(
    strategy: str | Strategy,
    *,
    market: str = "KRW-BTC",
    from_dt: datetime = datetime(2020, 1, 1, tzinfo=timezone.utc),
    to_dt: datetime | None = None,
    interval: str = "minute_240",
    cost: Cost | None = None,
    cache_dir: str = "outputs/data/upbit-cache",
    client=None,
    **params,
) -> BacktestResult:
    strat = _resolve(strategy, params)
    candles = load_candles(
        market=market, from_dt=from_dt, to_dt=to_dt, interval=interval, cache_dir=cache_dir, client=client
    )
    return run_backtest(strat, candles, cost or Cost())


def evaluate_candles(
    strategy: str | Strategy,
    candles: Sequence[Candle],
    *,
    cost: Cost | None = None,
    **params,
) -> BacktestResult:
    return run_backtest(_resolve(strategy, params), candles, cost or Cost())
