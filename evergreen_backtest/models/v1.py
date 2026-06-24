"""V1 model: 이동평균 추세 필터와 RSI 과매도 진입."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterator

from evergreen_backtest.data import CandleBar
from evergreen_backtest.modeling import ModelSignal, action_from_target, parse_double_range, parse_int_range
from evergreen_backtest.models.indicators import moving_average, wilder_rsi


RSI_PERIOD = 14
JAVA_PARAM_FIELDS = {"rsiBuy", "maLen", "maSlopeDays"}


@dataclass(frozen=True)
class StrategyParamsV1:
    fee_per_side: float
    slippage: float
    rsi_buy: float
    ma_len: int
    ma_slope_days: int


@dataclass(frozen=True)
class BacktestConfigV1:
    enabled: bool = True
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 1
    grid_rsi_range: str = "20:45:5"
    grid_ma_len_range: str = "20:120:10"
    grid_ma_slope_range: str = "1:9:2"

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation_ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee_per_side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top_k must be > 0")


class StrategyModelV1:
    def evaluate(self, bars: list[CandleBar], params: StrategyParamsV1) -> list[ModelSignal]:
        if bars is None or len(bars) < 2:
            raise ValueError("At least 2 bars are required")
        _validate_params(params)

        close = [bar.close for bar in bars]
        ma = moving_average(close, params.ma_len)
        rsi = wilder_rsi(close, RSI_PERIOD)
        target_close = [0.0] * len(bars)
        signals: list[ModelSignal] = []

        for i, bar in enumerate(bars):
            current_open = 0.0 if i == 0 else target_close[i - 1]
            slope_ok = (
                i - params.ma_slope_days >= 0
                and math.isfinite(ma[i])
                and math.isfinite(ma[i - params.ma_slope_days])
                and ma[i] > ma[i - params.ma_slope_days]
            )
            bull = math.isfinite(ma[i]) and close[i] > ma[i] and slope_ok
            setup_buy = bull and math.isfinite(rsi[i]) and rsi[i] < params.rsi_buy
            setup_sell = math.isfinite(ma[i]) and close[i] < ma[i]

            if not math.isfinite(ma[i]) or not math.isfinite(rsi[i]):
                target = 0.0
            elif setup_sell:
                target = 0.0
            elif setup_buy:
                target = 1.0
            else:
                target = current_open
            target_close[i] = target

            action = action_from_target(current_open, target)
            signals.append(
                ModelSignal(
                    timestamp=bar.timestamp,
                    open=bar.open,
                    close=bar.close,
                    action=action,
                    signal_reason=_resolve_signal_reason(action.value, setup_buy, setup_sell),
                    target_position_ratio=target,
                    diagnostics={
                        "ma": ma[i],
                        "rsi": rsi[i],
                        "setup_buy": setup_buy,
                        "setup_sell": setup_sell,
                    },
                )
            )
        return signals


def iter_parameter_grid(config: BacktestConfigV1) -> Iterator[StrategyParamsV1]:
    for rsi_buy in parse_double_range(config.grid_rsi_range):
        for ma_len in parse_int_range(config.grid_ma_len_range):
            for ma_slope_days in parse_int_range(config.grid_ma_slope_range):
                yield StrategyParamsV1(
                    fee_per_side=config.fee_per_side,
                    slippage=config.slippage,
                    rsi_buy=rsi_buy,
                    ma_len=ma_len,
                    ma_slope_days=ma_slope_days,
                )


def _validate_params(params: StrategyParamsV1) -> None:
    if params.fee_per_side < 0 or params.slippage < 0:
        raise ValueError("fee/slippage must be >= 0")
    if params.ma_len <= 0:
        raise ValueError("ma_len must be > 0")
    if params.ma_slope_days <= 0:
        raise ValueError("ma_slope_days must be > 0")
    if params.rsi_buy <= 0.0 or params.rsi_buy >= 100.0:
        raise ValueError("rsi_buy must be in (0, 100)")


def _resolve_signal_reason(action: str, setup_buy: bool, setup_sell: bool) -> str:
    if action == "BUY":
        return "BUY_MA_RSI"
    if action == "SELL":
        return "SELL_MA_BREAK"
    if setup_buy:
        return "SETUP_BUY"
    if setup_sell:
        return "SETUP_SELL"
    return "NONE"
