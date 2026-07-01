"""v3 — regime + ATR trailing stop, exiting whenever the regime leaves BULL (daily).

Spot adaptation: the original v3 sized the position by a volatility target (up to
2x leverage). This framework is binary spot (in / out), so the *sizing* is dropped
and only v3's entry/exit *timing* is kept: enter on BEAR->BULL, hold while BULL,
exit as soon as the regime is no longer BULL or the ATR trailing stop triggers.
Intended interval: ``days``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("v3")
class RegimeVolSpotStrategy(Strategy):
    def __init__(
        self,
        regime_ema_len: int = 200,
        atr_period: int = 14,
        atr_trail_multiplier: float = 3.0,
        regime_band: float = 0.02,
    ) -> None:
        if regime_ema_len <= 0 or atr_period <= 0:
            raise ValueError("regime_ema_len and atr_period must be > 0")
        if atr_trail_multiplier < 0.0:
            raise ValueError("atr_trail_multiplier must be >= 0")
        if not (0.0 <= regime_band < 1.0):
            raise ValueError("regime_band must be in [0, 1)")
        self.regime_ema_len = regime_ema_len
        self.atr_period = atr_period
        self.atr_trail_multiplier = atr_trail_multiplier
        self.regime_band = regime_band
        self._peak = math.nan
        self._was_in = False

    def warmup(self) -> int:
        return self.regime_ema_len

    def reset(self) -> None:
        self._peak = math.nan
        self._was_in = False

    def features(self, candles: Sequence[Candle]) -> dict[str, list[float]]:
        high = [c.high for c in candles]
        low = [c.low for c in candles]
        close = [c.close for c in candles]
        ema = ind.exponential_moving_average(close, self.regime_ema_len)
        return {
            "regime": ind.regime_codes(close, ema, self.regime_band),
            "atr": ind.wilder_atr(high, low, close, self.atr_period),
        }

    def _update_peak(self, in_position: bool, close: float) -> None:
        if in_position and not self._was_in:
            self._peak = close
        elif in_position:
            self._peak = max(self._peak, close) if math.isfinite(self._peak) else close
        else:
            self._peak = math.nan
        self._was_in = in_position

    def _trail_stop_hit(self, in_position: bool, close: float, atr: float) -> bool:
        if not in_position or self.atr_trail_multiplier <= 0.0:
            return False
        if not math.isfinite(atr) or not math.isfinite(self._peak):
            return False
        return close <= self._peak - (self.atr_trail_multiplier * atr)

    def decide(self, ctx: BarContext) -> Action:
        close = ctx.now.close
        in_position = ctx.in_position
        self._update_peak(in_position, close)
        regime = ctx.feature("regime")
        regime_prev = ctx.feature("regime", lag=1)
        atr = ctx.feature("atr")

        if in_position and (regime != ind.REGIME_BULL or self._trail_stop_hit(in_position, close, atr)):
            return Action.SELL
        if regime_prev == ind.REGIME_BEAR and regime == ind.REGIME_BULL:
            return Action.BUY
        return Action.HOLD
