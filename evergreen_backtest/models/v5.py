"""V5 model: 레짐/ATR에 변동성 상태별 ATR 배수를 적용."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Iterator

from evergreen_backtest.contracts import SignalAction
from evergreen_backtest.data import CandleBar
from evergreen_backtest.modeling import ModelSignal, action_from_target, parse_double_range, parse_positive_int_range
from evergreen_backtest.models.indicators import MarketRegime, exponential_moving_average, resolve_regimes, wilder_atr


FULL_POSITION_UNITS = 3
JAVA_PARAM_FIELDS = {
    "regimeEmaLen",
    "atrPeriod",
    "atrMultLowVol",
    "atrMultHighVol",
    "volRegimeLookback",
    "volRegimeThreshold",
    "regimeBand",
}


@dataclass(frozen=True)
class StrategyParamsV5:
    fee_per_side: float
    slippage: float
    regime_ema_len: int
    atr_period: int
    atr_mult_low_vol: float
    atr_mult_high_vol: float
    vol_regime_lookback: int
    vol_regime_threshold: float
    regime_band: float


@dataclass(frozen=True)
class BacktestConfigV5:
    enabled: bool = True
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 10
    grid_parallelism: int = max(1, os.cpu_count() or 1)
    grid_ma_len_range: str = "200:200:1"
    grid_atr_period_range: str = "14:14:1"
    grid_atr_mult_low_vol_range: str = "2:2:1"
    grid_atr_mult_high_vol_range: str = "4:4:1"
    grid_vol_regime_lookback_range: str = "30:30:1"
    grid_vol_regime_threshold_range: str = "0.7:0.7:0.1"
    grid_regime_band_range: str = "0.02:0.02:0.01"

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation_ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee_per_side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top_k must be > 0")
        if self.grid_parallelism <= 0:
            raise ValueError("grid_parallelism must be > 0")


class StrategyModelV5:
    def evaluate(self, bars: list[CandleBar], params: StrategyParamsV5) -> list[ModelSignal]:
        if bars is None or len(bars) < 2:
            raise ValueError("At least 2 daily bars are required")
        _validate_params(params)

        high = [bar.high for bar in bars]
        low = [bar.low for bar in bars]
        close = [bar.close for bar in bars]
        regime_ema = exponential_moving_average(close, params.regime_ema_len)
        atr = wilder_atr(high, low, close, params.atr_period)
        regimes, anchor, upper, lower = resolve_regimes(close, regime_ema, params.regime_band)
        atr_price_ratio = _resolve_atr_price_ratio(atr, close)
        vol_percentile, volatility_is_high = _resolve_volatility_states(
            atr_price_ratio,
            params.vol_regime_lookback,
            params.vol_regime_threshold,
        )
        atr_multiplier_applied = [
            params.atr_mult_high_vol if volatility_is_high[i] else params.atr_mult_low_vol
            for i in range(len(bars))
        ]

        target_units_close = [0] * len(bars)
        highest_close_since_entry = math.nan
        signals: list[ModelSignal] = []

        for i, bar in enumerate(bars):
            current_units = 0 if i == 0 else target_units_close[i - 1]
            previous_units = 0 if i <= 1 else target_units_close[i - 2]
            current_open = current_units / float(FULL_POSITION_UNITS)
            if current_units > previous_units:
                highest_close_since_entry = close[i]
            elif current_units > 0:
                highest_close_since_entry = max(highest_close_since_entry, close[i]) if math.isfinite(highest_close_since_entry) else close[i]
            else:
                highest_close_since_entry = math.nan

            setup_buy = _is_buy_setup(i, regimes)
            setup_sell = _is_sell_setup(i, regimes, current_units)
            atr_stop, trail_stop_triggered = _evaluate_atr_trail_stop(
                i,
                close,
                atr,
                highest_close_since_entry,
                current_units,
                atr_multiplier_applied[i],
            )

            target_units = current_units
            if trail_stop_triggered or setup_sell:
                target_units = 0
            elif setup_buy:
                target_units = FULL_POSITION_UNITS
            target_units_close[i] = target_units

            target = target_units / float(FULL_POSITION_UNITS)
            action = action_from_target(current_open, target)
            signals.append(
                ModelSignal(
                    timestamp=bar.timestamp,
                    open=bar.open,
                    close=bar.close,
                    action=action,
                    signal_reason=_resolve_signal_reason(action, setup_buy, setup_sell, trail_stop_triggered),
                    target_position_ratio=target,
                    diagnostics={
                        "ma": regime_ema[i],
                        "rsi": math.nan,
                        "setup_buy": setup_buy,
                        "setup_sell": setup_sell,
                        "trail_stop_triggered": trail_stop_triggered,
                        "regime": regimes[i].value,
                        "regime_anchor": anchor[i],
                        "regime_upper": upper[i],
                        "regime_lower": lower[i],
                        "volatility_is_high": volatility_is_high[i],
                        "atr_price_ratio": atr_price_ratio[i],
                        "vol_percentile": vol_percentile[i],
                        "atr_trail_multiplier_applied": atr_multiplier_applied[i],
                        "atr_trail_stop": atr_stop,
                    },
                )
            )
        return signals


def iter_parameter_grid(config: BacktestConfigV5) -> Iterator[StrategyParamsV5]:
    for ma_len in parse_positive_int_range(config.grid_ma_len_range, "grid_ma_len_range"):
        for atr_period in parse_positive_int_range(config.grid_atr_period_range, "grid_atr_period_range"):
            for atr_mult_low_vol in parse_double_range(config.grid_atr_mult_low_vol_range):
                for atr_mult_high_vol in parse_double_range(config.grid_atr_mult_high_vol_range):
                    for vol_lookback in parse_positive_int_range(config.grid_vol_regime_lookback_range, "grid_vol_regime_lookback_range"):
                        for vol_threshold in parse_double_range(config.grid_vol_regime_threshold_range):
                            for regime_band in parse_double_range(config.grid_regime_band_range):
                                yield StrategyParamsV5(
                                    fee_per_side=config.fee_per_side,
                                    slippage=config.slippage,
                                    regime_ema_len=ma_len,
                                    atr_period=atr_period,
                                    atr_mult_low_vol=atr_mult_low_vol,
                                    atr_mult_high_vol=atr_mult_high_vol,
                                    vol_regime_lookback=vol_lookback,
                                    vol_regime_threshold=vol_threshold,
                                    regime_band=regime_band,
                                )


def _is_buy_setup(index: int, regimes: list[MarketRegime]) -> bool:
    if index <= 0:
        return False
    return regimes[index - 1] == MarketRegime.BEAR and regimes[index] == MarketRegime.BULL


def _is_sell_setup(index: int, regimes: list[MarketRegime], current_open_units: int) -> bool:
    if current_open_units <= 0 or index <= 0:
        return False
    return regimes[index - 1] == MarketRegime.BULL and regimes[index] == MarketRegime.BEAR


def _evaluate_atr_trail_stop(
        index: int,
        close: list[float],
        atr: list[float],
        highest_close_since_entry: float,
        current_open_units: int,
        atr_trail_multiplier: float,
) -> tuple[float, bool]:
    if atr_trail_multiplier <= 0.0 or current_open_units <= 0:
        return (math.nan, False)
    if not math.isfinite(atr[index]) or not math.isfinite(highest_close_since_entry):
        return (math.nan, False)
    stop = highest_close_since_entry - (atr_trail_multiplier * atr[index])
    return (stop, close[index] <= stop)


def _resolve_atr_price_ratio(atr: list[float], close: list[float]) -> list[float]:
    out = [math.nan] * len(close)
    for i, close_price in enumerate(close):
        if math.isfinite(atr[i]) and math.isfinite(close_price) and close_price > 0.0:
            out[i] = atr[i] / close_price
    return out


def _resolve_volatility_states(
        atr_price_ratio: list[float],
        lookback: int,
        threshold: float,
) -> tuple[list[float], list[bool]]:
    percentile = [math.nan] * len(atr_price_ratio)
    is_high = [False] * len(atr_price_ratio)
    for i, current in enumerate(atr_price_ratio):
        start = max(0, i - lookback + 1)
        window = [value for value in atr_price_ratio[start : i + 1] if math.isfinite(value)]
        if not math.isfinite(current) or not window:
            continue
        below_or_equal = sum(1 for value in window if value <= current)
        rank = below_or_equal / float(len(window))
        percentile[i] = rank
        is_high[i] = rank >= threshold
    return percentile, is_high


def _validate_params(params: StrategyParamsV5) -> None:
    if params.fee_per_side < 0 or params.slippage < 0:
        raise ValueError("fee/slippage must be >= 0")
    if params.regime_ema_len <= 0 or params.atr_period <= 0:
        raise ValueError("ema and atr periods must be > 0")
    if params.atr_mult_low_vol < 0.0 or params.atr_mult_high_vol < 0.0:
        raise ValueError("atr multipliers must be >= 0")
    if params.atr_mult_high_vol < params.atr_mult_low_vol:
        raise ValueError("atr_mult_high_vol must be >= atr_mult_low_vol")
    if params.vol_regime_lookback <= 0:
        raise ValueError("vol_regime_lookback must be > 0")
    if params.vol_regime_threshold <= 0.0 or params.vol_regime_threshold > 1.0:
        raise ValueError("vol_regime_threshold must be in (0, 1]")
    if params.regime_band < 0.0 or params.regime_band >= 1.0:
        raise ValueError("regime_band must be in [0, 1)")


def _resolve_signal_reason(
        action: SignalAction,
        setup_buy: bool,
        setup_sell: bool,
        trail_stop_triggered: bool,
) -> str:
    if action == SignalAction.BUY:
        return "BUY_REGIME_VOL_STATE"
    if action == SignalAction.SELL and setup_sell and trail_stop_triggered:
        return "SELL_REGIME_AND_TRAIL_STOP"
    if action == SignalAction.SELL and trail_stop_triggered:
        return "SELL_TRAIL_STOP"
    if action == SignalAction.SELL:
        return "SELL_REGIME_TRANSITION"
    if setup_buy:
        return "SETUP_BUY"
    if setup_sell:
        return "SETUP_SELL"
    return "NONE"
