"""v5 — v2 regime + ATR trailing stop with a volatility-state-dependent multiplier.

Same as v2, but the ATR trail multiplier switches between ``atr_mult_low_vol`` and
``atr_mult_high_vol`` depending on whether the ATR/price ratio is in a high-vol
state (its rolling percentile over ``vol_regime_lookback`` >= ``vol_regime_threshold``).
Stateful (post-entry peak). Intended interval: ``days``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("v5")
class RegimeVolStateStrategy(Strategy):
    def __init__(
        self,
        regime_ema_len: int = 200,
        atr_period: int = 14,
        atr_mult_low_vol: float = 2.0,
        atr_mult_high_vol: float = 4.0,
        vol_regime_lookback: int = 30,
        vol_regime_threshold: float = 0.7,
        regime_band: float = 0.02,
    ) -> None:
        if regime_ema_len <= 0 or atr_period <= 0 or vol_regime_lookback <= 0:
            raise ValueError("periods must be > 0")
        if atr_mult_low_vol < 0.0 or atr_mult_high_vol < atr_mult_low_vol:
            raise ValueError("need 0 <= atr_mult_low_vol <= atr_mult_high_vol")
        if not (0.0 < vol_regime_threshold <= 1.0):
            raise ValueError("vol_regime_threshold must be in (0, 1]")
        if not (0.0 <= regime_band < 1.0):
            raise ValueError("regime_band must be in [0, 1)")
        self.regime_ema_len = regime_ema_len
        self.atr_period = atr_period
        self.atr_mult_low_vol = atr_mult_low_vol
        self.atr_mult_high_vol = atr_mult_high_vol
        self.vol_regime_lookback = vol_regime_lookback
        self.vol_regime_threshold = vol_regime_threshold
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
        atr = ind.wilder_atr(high, low, close, self.atr_period)
        atr_mult = self._atr_multiplier_series(atr, close)
        return {"regime": ind.regime_codes(close, ema, self.regime_band), "atr": atr, "atr_mult": atr_mult}

    def _atr_multiplier_series(self, atr: list[float], close: list[float]) -> list[float]:
        ratio = [
            atr[i] / close[i] if math.isfinite(atr[i]) and math.isfinite(close[i]) and close[i] > 0.0 else math.nan
            for i in range(len(close))
        ]
        out = [self.atr_mult_low_vol] * len(close)
        for i, current in enumerate(ratio):
            start = max(0, i - self.vol_regime_lookback + 1)
            window = [v for v in ratio[start : i + 1] if math.isfinite(v)]
            if not math.isfinite(current) or not window:
                continue
            rank = sum(1 for v in window if v <= current) / float(len(window))
            out[i] = self.atr_mult_high_vol if rank >= self.vol_regime_threshold else self.atr_mult_low_vol
        return out

    def _update_peak(self, in_position: bool, close: float) -> None:
        if in_position and not self._was_in:
            self._peak = close
        elif in_position:
            self._peak = max(self._peak, close) if math.isfinite(self._peak) else close
        else:
            self._peak = math.nan
        self._was_in = in_position

    def _trail_stop_hit(self, in_position: bool, close: float, atr: float, multiplier: float) -> bool:
        if not in_position or multiplier <= 0.0:
            return False
        if not math.isfinite(atr) or not math.isfinite(self._peak):
            return False
        return close <= self._peak - (multiplier * atr)

    def decide(self, ctx: BarContext) -> Action:
        close = ctx.now.close
        in_position = ctx.in_position
        self._update_peak(in_position, close)
        regime = ctx.feature("regime")
        regime_prev = ctx.feature("regime", lag=1)
        atr = ctx.feature("atr")
        multiplier = ctx.feature("atr_mult")

        sell_setup = in_position and regime_prev == ind.REGIME_BULL and regime == ind.REGIME_BEAR
        if sell_setup or self._trail_stop_hit(in_position, close, atr, multiplier):
            return Action.SELL
        if regime_prev == ind.REGIME_BEAR and regime == ind.REGIME_BULL:
            return Action.BUY
        return Action.HOLD
