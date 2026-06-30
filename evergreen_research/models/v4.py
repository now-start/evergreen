"""V4 model: v2 레짐/ATR에 증분 주간 EMA 필터를 추가."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Iterator

from evergreen_research.contracts import SignalAction
from evergreen_research.data import CandleBar
from evergreen_research.modeling import ModelSignal, action_from_target, parse_double_range, parse_positive_int_range
from evergreen_research.models.indicators import MarketRegime, exponential_moving_average, resolve_regimes, wilder_atr


FULL_POSITION_UNITS = 3
JAVA_PARAM_FIELDS = {"regimeEmaLen", "atrPeriod", "atrTrailMultiplier", "regimeBand", "weeklyEmaLen"}


@dataclass(frozen=True)
class StrategyParamsV4:
    fee_per_side: float
    slippage: float
    regime_ema_len: int
    atr_period: int
    atr_trail_multiplier: float
    regime_band: float
    weekly_ema_len: int


@dataclass(frozen=True)
class BacktestConfigV4:
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
    grid_weekly_ema_len_range: str = "20:20:1"

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation_ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee_per_side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top_k must be > 0")
        if self.grid_parallelism <= 0:
            raise ValueError("grid_parallelism must be > 0")


class StrategyModelV4:
    def evaluate(self, bars: list[CandleBar], params: StrategyParamsV4) -> list[ModelSignal]:
        if bars is None or len(bars) < 2:
            raise ValueError("At least 2 daily bars are required")
        _validate_params(params)

        high = [bar.high for bar in bars]
        low = [bar.low for bar in bars]
        close = [bar.close for bar in bars]
        regime_ema = exponential_moving_average(close, params.regime_ema_len)
        atr = wilder_atr(high, low, close, params.atr_period)
        regimes, anchor, upper, lower = resolve_regimes(close, regime_ema, params.regime_band)
        weekly_close, weekly_ema, weekly_bullish = _build_weekly_filter(bars, params.weekly_ema_len)

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

            setup_buy = _is_buy_setup(i, regimes) and weekly_bullish[i]
            setup_sell = _is_sell_setup(i, regimes, current_units)
            atr_stop, trail_stop_triggered = _evaluate_atr_trail_stop(
                i,
                close,
                atr,
                highest_close_since_entry,
                current_units,
                params,
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
                        "weekly_close": weekly_close[i],
                        "weekly_ema": weekly_ema[i],
                        "weekly_bullish": weekly_bullish[i],
                        "rsi": math.nan,
                        "setup_buy": setup_buy,
                        "setup_sell": setup_sell,
                        "trail_stop_triggered": trail_stop_triggered,
                        "regime": regimes[i].value,
                        "regime_anchor": anchor[i],
                        "regime_upper": upper[i],
                        "regime_lower": lower[i],
                        "atr_trail_stop": atr_stop,
                    },
                )
            )
        return signals


def iter_parameter_grid(config: BacktestConfigV4) -> Iterator[StrategyParamsV4]:
    for ma_len in parse_positive_int_range(config.grid_ma_len_range, "grid_ma_len_range"):
        for atr_period in parse_positive_int_range(config.grid_atr_period_range, "grid_atr_period_range"):
            for atr_trail_multiplier in parse_double_range(config.grid_atr_trail_mult_range):
                for regime_band in parse_double_range(config.grid_regime_band_range):
                    for weekly_ema_len in parse_positive_int_range(config.grid_weekly_ema_len_range, "grid_weekly_ema_len_range"):
                        yield StrategyParamsV4(
                            fee_per_side=config.fee_per_side,
                            slippage=config.slippage,
                            regime_ema_len=ma_len,
                            atr_period=atr_period,
                            atr_trail_multiplier=atr_trail_multiplier,
                            regime_band=regime_band,
                            weekly_ema_len=weekly_ema_len,
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
        params: StrategyParamsV4,
) -> tuple[float, bool]:
    if params.atr_trail_multiplier <= 0.0 or current_open_units <= 0:
        return (math.nan, False)
    if not math.isfinite(atr[index]) or not math.isfinite(highest_close_since_entry):
        return (math.nan, False)
    stop = highest_close_since_entry - (params.atr_trail_multiplier * atr[index])
    return (stop, close[index] <= stop)


def _build_weekly_filter(bars: list[CandleBar], weekly_ema_len: int) -> tuple[list[float], list[float], list[bool]]:
    week_keys: list[tuple[int, int]] = []
    weekly_closes: list[float] = []
    weekly_close_by_day = [math.nan] * len(bars)
    weekly_ema_by_day = [math.nan] * len(bars)
    weekly_bull_by_day = [False] * len(bars)

    for i, bar in enumerate(bars):
        iso = bar.timestamp.isocalendar()
        key = (iso.year, iso.week)
        if not week_keys or week_keys[-1] != key:
            week_keys.append(key)
            weekly_closes.append(bar.close)
        else:
            weekly_closes[-1] = bar.close

        weekly_ema_series = exponential_moving_average(weekly_closes, weekly_ema_len)
        weekly_close_by_day[i] = weekly_closes[-1]
        weekly_ema_by_day[i] = weekly_ema_series[-1]
        weekly_bull_by_day[i] = math.isfinite(weekly_ema_series[-1]) and weekly_closes[-1] >= weekly_ema_series[-1]

    return weekly_close_by_day, weekly_ema_by_day, weekly_bull_by_day


def _validate_params(params: StrategyParamsV4) -> None:
    if params.fee_per_side < 0 or params.slippage < 0:
        raise ValueError("fee/slippage must be >= 0")
    if params.regime_ema_len <= 0 or params.weekly_ema_len <= 0:
        raise ValueError("ema lengths must be > 0")
    if params.atr_period <= 0 or params.atr_trail_multiplier < 0.0:
        raise ValueError("atr parameters are invalid")
    if params.regime_band < 0.0 or params.regime_band >= 1.0:
        raise ValueError("regime_band must be in [0, 1)")


def _resolve_signal_reason(
        action: SignalAction,
        setup_buy: bool,
        setup_sell: bool,
        trail_stop_triggered: bool,
) -> str:
    if action == SignalAction.BUY:
        return "BUY_REGIME_WEEKLY_FILTER"
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
