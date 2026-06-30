from __future__ import annotations

from enum import Enum
import math


class MarketRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    UNKNOWN = "UNKNOWN"


def moving_average(values: list[float], length: int) -> list[float]:
    n = len(values)
    out = [math.nan] * n
    if length <= 0:
        return out
    window_sum = 0.0
    for i, value in enumerate(values):
        window_sum += value
        if i >= length:
            window_sum -= values[i - length]
        if i >= length - 1:
            out[i] = window_sum / length
    return out


def exponential_moving_average(values: list[float], length: int) -> list[float]:
    n = len(values)
    ema = [math.nan] * n
    if length <= 0 or n < length:
        return ema

    seed = sum(values[:length])
    ema[length - 1] = seed / length

    alpha = 2.0 / (length + 1.0)
    for i in range(length, n):
        ema[i] = (alpha * values[i]) + ((1.0 - alpha) * ema[i - 1])
    return ema


def wilder_rsi(close: list[float], period: int) -> list[float]:
    n = len(close)
    rsi = [math.nan] * n
    if period <= 0 or n <= period:
        return rsi

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        change = close[i] - close[i - 1]
        gains[i] = max(0.0, change)
        losses[i] = max(0.0, -change)

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    rsi[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, n):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        rsi[i] = _rsi_from_averages(avg_gain, avg_loss)
    return rsi


def wilder_atr(high: list[float], low: list[float], close: list[float], period: int) -> list[float]:
    n = len(close)
    atr = [math.nan] * n
    if period <= 0 or n < period:
        return atr

    tr = [0.0] * n
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        high_low = high[i] - low[i]
        high_prev_close = abs(high[i] - close[i - 1])
        low_prev_close = abs(low[i] - close[i - 1])
        tr[i] = max(high_low, high_prev_close, low_prev_close)

    total = sum(tr[:period])
    first = period - 1
    atr[first] = total / period
    for i in range(period, n):
        atr[i] = ((atr[i - 1] * (period - 1)) + tr[i]) / period
    return atr


def resolve_regimes(
        close: list[float],
        anchor: list[float],
        regime_band: float,
) -> tuple[list[MarketRegime], list[float], list[float], list[float]]:
    n = len(close)
    regimes = [MarketRegime.UNKNOWN] * n
    upper = [math.nan] * n
    lower = [math.nan] * n

    for i in range(n):
        if not math.isfinite(anchor[i]):
            regimes[i] = MarketRegime.UNKNOWN
            continue

        upper[i] = anchor[i] * (1.0 + regime_band)
        lower[i] = anchor[i] * (1.0 - regime_band)

        previous = MarketRegime.UNKNOWN if i == 0 else regimes[i - 1]
        if close[i] > upper[i]:
            regimes[i] = MarketRegime.BULL
        elif close[i] < lower[i]:
            regimes[i] = MarketRegime.BEAR
        elif previous != MarketRegime.UNKNOWN:
            regimes[i] = previous
        elif close[i] > anchor[i]:
            regimes[i] = MarketRegime.BULL
        elif close[i] < anchor[i]:
            regimes[i] = MarketRegime.BEAR
        else:
            regimes[i] = MarketRegime.UNKNOWN

    return regimes, anchor, upper, lower


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
