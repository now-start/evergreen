"""v4 — v2 regime + ATR trailing stop, with a weekly-EMA trend filter on entries.

Same as v2, but a BEAR->BULL entry only fires when the current weekly close is at
or above its ``weekly_ema_len``-period weekly EMA. Stateful (post-entry peak).
Intended interval: ``days``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("v4")
class RegimeWeeklyStrategy(Strategy):
    def __init__(
        self,
        regime_ema_len: int = 200,
        atr_period: int = 14,
        atr_trail_multiplier: float = 3.0,
        regime_band: float = 0.02,
        weekly_ema_len: int = 20,
    ) -> None:
        if regime_ema_len <= 0 or atr_period <= 0 or weekly_ema_len <= 0:
            raise ValueError("periods must be > 0")
        if atr_trail_multiplier < 0.0:
            raise ValueError("atr_trail_multiplier must be >= 0")
        if not (0.0 <= regime_band < 1.0):
            raise ValueError("regime_band must be in [0, 1)")
        self.regime_ema_len = regime_ema_len
        self.atr_period = atr_period
        self.atr_trail_multiplier = atr_trail_multiplier
        self.regime_band = regime_band
        self.weekly_ema_len = weekly_ema_len
        self._peak = math.nan
        self._was_in = False

    def warmup(self) -> int:
        return max(self.regime_ema_len, self.atr_period)

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
            "weekly_bull": self._weekly_bullish(candles),
        }

    def _weekly_bullish(self, candles: Sequence[Candle]) -> list[float]:
        week_keys: list[tuple[int, int]] = []
        weekly_closes: list[float] = []
        out = [0.0] * len(candles)
        for i, candle in enumerate(candles):
            iso = candle.timestamp.isocalendar()
            key = (iso[0], iso[1])  # (iso year, iso week)
            if not week_keys or week_keys[-1] != key:
                week_keys.append(key)
                weekly_closes.append(candle.close)
            else:
                weekly_closes[-1] = candle.close
            weekly_ema = ind.exponential_moving_average(weekly_closes, self.weekly_ema_len)
            last_ema = weekly_ema[-1]
            out[i] = 1.0 if (math.isfinite(last_ema) and weekly_closes[-1] >= last_ema) else 0.0
        return out

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
        weekly_bull = ctx.feature("weekly_bull") >= 0.5

        sell_setup = in_position and regime_prev == ind.REGIME_BULL and regime == ind.REGIME_BEAR
        if sell_setup or self._trail_stop_hit(in_position, close, atr):
            return Action.SELL
        if regime_prev == ind.REGIME_BEAR and regime == ind.REGIME_BULL and weekly_bull:
            return Action.BUY
        return Action.HOLD
