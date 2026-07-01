"""trend2 — faithful spot port of the conference winner 'agent_05' rule.

    trend2 = ema_gap_12 + 0.7*ema_gap_26 + ma_gap_10 + ma_gap_20
             + macd + macd_signal + 0.5*(return_lag_5 + return_lag_10)

Decision (spot):
    * BUY  when trend2 > 0 and |trend2| / scale >= buy_cutoff
    * SELL when trend2 < 0 and |trend2| / scale >= sell_cutoff  (only while holding)

``scale`` is the 70th percentile of |trend2| fit on the first ``train_fraction``
of the series. Trading is suppressed (score = NaN -> HOLD) until that training
window ends, so every traded bar is normalized by a scale fit on strictly earlier
data -> no look-ahead. Pass ``rule_scale`` to use a fixed scale and trade from
warmup instead.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("trend2")
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
