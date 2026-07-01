"""v1 — 이동평균 트렌드 필터 + RSI 과매도 진입, MA 이탈 청산 (일봉).

close > MA이고, MA가 ``ma_slope_days``기간 동안 상승 중이고, RSI < ``rsi_buy``일 때 BUY.
close < MA일 때 SELL(청산). 상태 없음(stateless): MA와 RSI는 미리 계산된 인과적 시리즈.
권장 인터벌: ``days``. 기본값은 무난한 중간값들 — 자유롭게 튜닝하면 됨.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("v1")
class MaRsiStrategy(Strategy):
    def __init__(self, rsi_buy: float = 35.0, ma_len: int = 60, ma_slope_days: int = 5, rsi_period: int = 14) -> None:
        if not (0.0 < rsi_buy < 100.0):
            raise ValueError("rsi_buy must be in (0, 100)")
        if ma_len <= 0 or ma_slope_days <= 0 or rsi_period <= 1:
            raise ValueError("ma_len, ma_slope_days must be > 0 and rsi_period > 1")
        self.rsi_buy = rsi_buy
        self.ma_len = ma_len
        self.ma_slope_days = ma_slope_days
        self.rsi_period = rsi_period

    def warmup(self) -> int:
        return max(self.ma_len - 1 + self.ma_slope_days, self.rsi_period)

    def features(self, candles: Sequence[Candle]) -> dict[str, list[float]]:
        close = [c.close for c in candles]
        return {"ma": ind.moving_average(close, self.ma_len), "rsi": ind.wilder_rsi(close, self.rsi_period)}

    def decide(self, ctx: BarContext) -> Action:
        ma = ctx.feature("ma")
        rsi = ctx.feature("rsi")
        if not math.isfinite(ma) or not math.isfinite(rsi):
            return Action.HOLD
        close = ctx.now.close
        if close < ma:
            return Action.SELL  # 이미 flat이면 엔진이 이걸 HOLD로 무너뜨림
        ma_prev = ctx.feature("ma", lag=self.ma_slope_days)
        slope_ok = math.isfinite(ma_prev) and ma > ma_prev
        if close > ma and slope_ok and rsi < self.rsi_buy:
            return Action.BUY
        return Action.HOLD
