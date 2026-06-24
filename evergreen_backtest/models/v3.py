"""V3 model: 레짐/ATR에 변동성 목표 비중을 결합."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Iterator

from evergreen_backtest.contracts import SignalAction
from evergreen_backtest.data import CandleBar
from evergreen_backtest.modeling import ModelSignal, action_from_target, parse_double_range, parse_positive_int_range
from evergreen_backtest.models.indicators import MarketRegime, exponential_moving_average, resolve_regimes, wilder_atr


JAVA_PARAM_FIELDS = {
    "regimeEmaLen",
    "atrPeriod",
    "atrTrailMultiplier",
    "regimeBand",
    "volTarget",
    "maxLeverage",
    "minExposure",
}


@dataclass(frozen=True)
class StrategyParamsV3:
    fee_per_side: float
    slippage: float
    regime_ema_len: int
    atr_period: int
    atr_trail_multiplier: float
    regime_band: float
    vol_target: float
    max_leverage: float
    min_exposure: float


@dataclass(frozen=True)
class BacktestConfigV3:
    enabled: bool = True
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 10
    grid_parallelism: int = max(1, os.cpu_count() or 1)
    grid_ma_len_range: str = "200:200:1"
    grid_atr_period_range: str = "14:14:1"
    grid_atr_trail_mult_range: str = "3:3:1"
    grid_regime_band_range: str = "0.02:0.02:0.01"
    grid_vol_target_range: str = "0.02:0.02:0.01"
    grid_max_leverage_range: str = "2:2:0.5"
    grid_min_exposure_range: str = "0:0:1"

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation_ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee_per_side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top_k must be > 0")
        if self.grid_parallelism <= 0:
            raise ValueError("grid_parallelism must be > 0")


class StrategyModelV3:
    def evaluate(self, bars: list[CandleBar], params: StrategyParamsV3) -> list[ModelSignal]:
        if bars is None or len(bars) < 2:
            raise ValueError("At least 2 daily bars are required")
        _validate_params(params)

        high = [bar.high for bar in bars]
        low = [bar.low for bar in bars]
        close = [bar.close for bar in bars]
        regime_ema = exponential_moving_average(close, params.regime_ema_len)
        atr = wilder_atr(high, low, close, params.atr_period)
        regimes, anchor, upper, lower = resolve_regimes(close, regime_ema, params.regime_band)
        vol_proxy = _resolve_vol_proxy(atr, close)

        target_close = [0.0] * len(bars)
        highest_close_since_entry = math.nan
        signals: list[ModelSignal] = []

        for i, bar in enumerate(bars):
            current_open = 0.0 if i == 0 else target_close[i - 1]
            previous_open = 0.0 if i <= 1 else target_close[i - 2]
            if current_open > previous_open and current_open > 0.0:
                highest_close_since_entry = close[i]
            elif current_open > 0.0:
                highest_close_since_entry = max(highest_close_since_entry, close[i]) if math.isfinite(highest_close_since_entry) else close[i]
            else:
                highest_close_since_entry = math.nan

            buy_setup = _is_buy_setup(i, regimes)
            sell_setup = _is_sell_setup(i, regimes, current_open)
            atr_stop, trail_stop_triggered = _evaluate_atr_trail_stop(
                i,
                close,
                atr,
                highest_close_since_entry,
                current_open,
                params,
            )

            bullish_active = regimes[i] == MarketRegime.BULL
            should_zero = (not bullish_active) or trail_stop_triggered or sell_setup
            desired_exposure = _target_exposure_from_vol_proxy(vol_proxy[i], params)
            target = 0.0
            if not should_zero and (buy_setup or current_open > 0.0):
                target = desired_exposure
            target_close[i] = target

            action = action_from_target(current_open, target)
            signals.append(
                ModelSignal(
                    timestamp=bar.timestamp,
                    open=bar.open,
                    close=bar.close,
                    action=action,
                    signal_reason=_resolve_signal_reason(action, buy_setup, sell_setup, trail_stop_triggered),
                    target_position_ratio=target,
                    diagnostics={
                        "ma": regime_ema[i],
                        "rsi": math.nan,
                        "setup_buy": buy_setup,
                        "setup_sell": sell_setup,
                        "trail_stop_triggered": trail_stop_triggered,
                        "regime": regimes[i].value,
                        "regime_anchor": anchor[i],
                        "regime_upper": upper[i],
                        "regime_lower": lower[i],
                        "atr": atr[i],
                        "vol_proxy": vol_proxy[i],
                        "target_exposure": target,
                        "atr_trail_stop": atr_stop,
                    },
                )
            )
        return signals


def iter_parameter_grid(config: BacktestConfigV3) -> Iterator[StrategyParamsV3]:
    for ma_len in parse_positive_int_range(config.grid_ma_len_range, "grid_ma_len_range"):
        for atr_period in parse_positive_int_range(config.grid_atr_period_range, "grid_atr_period_range"):
            for atr_trail_multiplier in parse_double_range(config.grid_atr_trail_mult_range):
                for regime_band in parse_double_range(config.grid_regime_band_range):
                    for vol_target in parse_double_range(config.grid_vol_target_range):
                        for max_leverage in parse_double_range(config.grid_max_leverage_range):
                            for min_exposure in parse_double_range(config.grid_min_exposure_range):
                                yield StrategyParamsV3(
                                    fee_per_side=config.fee_per_side,
                                    slippage=config.slippage,
                                    regime_ema_len=ma_len,
                                    atr_period=atr_period,
                                    atr_trail_multiplier=atr_trail_multiplier,
                                    regime_band=regime_band,
                                    vol_target=vol_target,
                                    max_leverage=max_leverage,
                                    min_exposure=min_exposure,
                                )


def _is_buy_setup(index: int, regimes: list[MarketRegime]) -> bool:
    if index <= 0:
        return False
    return regimes[index - 1] == MarketRegime.BEAR and regimes[index] == MarketRegime.BULL


def _is_sell_setup(index: int, regimes: list[MarketRegime], current_open_exposure: float) -> bool:
    if current_open_exposure <= 0.0 or index <= 0:
        return False
    return regimes[index - 1] == MarketRegime.BULL and regimes[index] == MarketRegime.BEAR


def _evaluate_atr_trail_stop(
        index: int,
        close: list[float],
        atr: list[float],
        highest_close_since_entry: float,
        current_open_exposure: float,
        params: StrategyParamsV3,
) -> tuple[float, bool]:
    if params.atr_trail_multiplier <= 0.0 or current_open_exposure <= 0.0:
        return (math.nan, False)
    if not math.isfinite(atr[index]) or not math.isfinite(highest_close_since_entry):
        return (math.nan, False)
    stop = highest_close_since_entry - (params.atr_trail_multiplier * atr[index])
    return (stop, close[index] <= stop)


def _resolve_vol_proxy(atr: list[float], close: list[float]) -> list[float]:
    values = [math.nan] * len(close)
    for i, close_price in enumerate(close):
        if math.isfinite(atr[i]) and close_price > 0.0:
            values[i] = atr[i] / close_price
    return values


def _target_exposure_from_vol_proxy(vol_proxy: float, params: StrategyParamsV3) -> float:
    if not math.isfinite(vol_proxy) or vol_proxy <= 0.0:
        return params.min_exposure
    raw = params.vol_target / vol_proxy
    return min(params.max_leverage, max(params.min_exposure, raw))


def _validate_params(params: StrategyParamsV3) -> None:
    if params.fee_per_side < 0 or params.slippage < 0:
        raise ValueError("fee/slippage must be >= 0")
    if params.regime_ema_len <= 0:
        raise ValueError("regime_ema_len must be > 0")
    if params.atr_period <= 0 or params.atr_trail_multiplier < 0.0:
        raise ValueError("atr parameters are invalid")
    if params.regime_band < 0.0 or params.regime_band >= 1.0:
        raise ValueError("regime_band must be in [0, 1)")
    if params.vol_target <= 0.0 or params.max_leverage <= 0.0 or params.min_exposure < 0.0:
        raise ValueError("volatility exposure parameters are invalid")
    if params.min_exposure > params.max_leverage:
        raise ValueError("min_exposure must be <= max_leverage")


def _resolve_signal_reason(
        action: SignalAction,
        setup_buy: bool,
        setup_sell: bool,
        trail_stop_triggered: bool,
) -> str:
    if action == SignalAction.BUY:
        return "BUY_VOL_TARGET_REGIME"
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
