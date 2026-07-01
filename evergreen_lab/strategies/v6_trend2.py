"""trend2 — 컨퍼런스 우승작 'agent_05' 규칙을 스팟으로 충실하게 포팅한 것.

    trend2 = ema_gap_12 + 0.7*ema_gap_26 + ma_gap_10 + ma_gap_20
             + macd + macd_signal + 0.5*(return_lag_5 + return_lag_10)

결정 (스팟):
    * trend2 > 0이고 |trend2| / scale >= buy_cutoff이면 BUY
    * trend2 < 0이고 |trend2| / scale >= sell_cutoff이면 SELL (보유 중일 때만)

``scale``은 시리즈의 첫 ``train_fraction`` 구간에 맞춘(fit) |trend2|의 70번째
백분위수다. 그 학습 구간이 끝나기 전까지는 거래가 억제되며(score = NaN -> HOLD),
그래서 거래되는 모든 바는 엄격히 더 이전 데이터로 맞춘 scale로 정규화된다
-> 미리보기 없음. 고정된 scale을 쓰고 warmup부터 바로 거래하려면 ``rule_scale``을
넘기면 된다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("v6")
class Trend2Strategy(Strategy):
    def __init__(
        self,
        buy_cutoff: float = 0.055,
        sell_cutoff: float = 0.25,
        rule_scale: float | None = None,
        train_fraction: float = 0.6,
    ) -> None:
        if buy_cutoff < 0.0 or sell_cutoff < 0.0:
            raise ValueError("cutoffs must be >= 0")
        if rule_scale is not None and rule_scale <= 0.0:
            raise ValueError("rule_scale must be > 0 when provided")
        if not (0.0 < train_fraction <= 1.0):
            raise ValueError("train_fraction must be in (0, 1]")
        self.buy_cutoff = buy_cutoff
        self.sell_cutoff = sell_cutoff
        self.rule_scale = rule_scale
        self.train_fraction = train_fraction

    def warmup(self) -> int:
        return 35

    def features(self, candles: Sequence[Candle]) -> dict[str, list[float]]:
        close = [candle.close for candle in candles]
        trend2 = ind.trend2_series(close)
        if self.rule_scale is not None:
            scale = self.rule_scale
            trade_from = 0
        else:
            trade_from = max(1, int(len(trend2) * self.train_fraction))
            scale = ind.robust_scale_q70([abs(v) for v in trend2[:trade_from]])
        score = [
            abs(v) / scale if (i >= trade_from and math.isfinite(v) and scale > 0.0) else math.nan
            for i, v in enumerate(trend2)
        ]
        return {"trend2": trend2, "score": score}

    def decide(self, ctx: BarContext) -> Action:
        trend2 = ctx.feature("trend2")
        score = ctx.feature("score")
        if not math.isfinite(trend2) or not math.isfinite(score):
            return Action.HOLD
        if trend2 > 0.0 and score >= self.buy_cutoff:
            return Action.BUY
        if trend2 < 0.0 and score >= self.sell_cutoff and ctx.in_position:
            return Action.SELL
        return Action.HOLD
