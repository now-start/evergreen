"""Causal technical indicators. Every series is aligned to the input and uses
only past/current values, so reading ``series[i]`` never leaks the future.

EMA uses ``adjust=False`` seeded at the first finite value and MACD is built the
same way pandas ``ewm(adjust=False, min_periods=...)`` does, so these match the
feature definitions the strategies were validated against.
"""

from __future__ import annotations

import math


def gap(current: float, anchor: float) -> float:
    """Relative gap ``current/anchor - 1``; 0.0 when either side is unusable."""
    if not math.isfinite(current) or not math.isfinite(anchor) or anchor <= 0.0:
        return 0.0
    return (current / anchor) - 1.0


def safe(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def ewm_adjust_false(values: list[float], length: int) -> list[float]:
    out = [math.nan] * len(values)
    if length <= 0:
        return out
    alpha = 2.0 / (length + 1.0)
    ema = math.nan
    finite = 0
    for i, value in enumerate(values):
        if not math.isfinite(value):
            continue
        ema = value if not math.isfinite(ema) else (alpha * value) + ((1.0 - alpha) * ema)
        finite += 1
        if finite >= length:
            out[i] = ema
    return out


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


def return_lag_series(close: list[float], lag: int) -> list[float]:
    out = [math.nan] * len(close)
    for i in range(lag, len(close)):
        previous, current = close[i - lag], close[i]
        if previous > 0.0 and math.isfinite(previous) and math.isfinite(current):
            out[i] = (current / previous) - 1.0
    return out


def macd(close: list[float]) -> tuple[list[float], list[float]]:
    ema12 = ewm_adjust_false(close, 12)
    ema26 = ewm_adjust_false(close, 26)
    raw = [
        ema12[i] - ema26[i] if math.isfinite(ema12[i]) and math.isfinite(ema26[i]) else math.nan
        for i in range(len(close))
    ]
    signal_raw = ewm_adjust_false(raw, 9)
    line = [
        raw[i] / close[i] if math.isfinite(raw[i]) and math.isfinite(close[i]) and close[i] > 0.0 else math.nan
        for i in range(len(close))
    ]
    signal = [
        signal_raw[i] / close[i]
        if math.isfinite(signal_raw[i]) and math.isfinite(close[i]) and close[i] > 0.0
        else math.nan
        for i in range(len(close))
    ]
    return line, signal


def trend2_series(close: list[float]) -> list[float]:
    """The conference 'trend2' rule signal (see strategies/trend2.py)."""
    ema12 = ewm_adjust_false(close, 12)
    ema26 = ewm_adjust_false(close, 26)
    ma10 = moving_average(close, 10)
    ma20 = moving_average(close, 20)
    macd_line, macd_signal = macd(close)
    ret5 = return_lag_series(close, 5)
    ret10 = return_lag_series(close, 10)

    out = [math.nan] * len(close)
    for i, price in enumerate(close):
        if price <= 0.0 or not math.isfinite(price):
            continue
        ema_component = gap(price, ema12[i]) + (0.7 * gap(price, ema26[i]))
        ma_component = gap(price, ma10[i]) + gap(price, ma20[i])
        macd_component = safe(macd_line[i]) + safe(macd_signal[i])
        momentum_component = 0.5 * (safe(ret5[i]) + safe(ret10[i]))
        out[i] = ema_component + ma_component + macd_component + momentum_component
    return out


def quantile_linear(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    position = (len(xs) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return xs[lower]
    weight = position - lower
    return (xs[lower] * (1.0 - weight)) + (xs[upper] * weight)


def robust_scale_q70(abs_values: list[float]) -> float:
    """70th percentile of positive-finite magnitudes; 1.0 fallback."""
    xs = [v for v in abs_values if math.isfinite(v) and v > 0.0]
    if not xs:
        return 1.0
    scale = quantile_linear(xs, 0.70)
    if math.isfinite(scale) and scale > 1e-12:
        return scale
    midpoint = xs[len(xs) // 2]
    return midpoint if math.isfinite(midpoint) and midpoint > 1e-12 else 1.0


# --- indicators for the v1..v5 daily strategies (ported from the legacy package) ---

REGIME_BULL = 1.0
REGIME_BEAR = -1.0
REGIME_UNKNOWN = 0.0


def exponential_moving_average(values: list[float], length: int) -> list[float]:
    """SMA-seeded EMA (the seeding the v1..v5 regime filters were validated with)."""
    n = len(values)
    ema = [math.nan] * n
    if length <= 0 or n < length:
        return ema
    ema[length - 1] = sum(values[:length]) / length
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


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def wilder_atr(high: list[float], low: list[float], close: list[float], period: int) -> list[float]:
    n = len(close)
    atr = [math.nan] * n
    if period <= 0 or n < period:
        return atr
    tr = [0.0] * n
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        atr[i] = ((atr[i - 1] * (period - 1)) + tr[i]) / period
    return atr


def regime_codes(close: list[float], anchor: list[float], band: float) -> list[float]:
    """BULL(1.0) / BEAR(-1.0) / UNKNOWN(0.0) with a hysteresis band around ``anchor``."""
    n = len(close)
    out = [REGIME_UNKNOWN] * n
    for i in range(n):
        a = anchor[i]
        if not math.isfinite(a):
            out[i] = REGIME_UNKNOWN
            continue
        upper, lower = a * (1.0 + band), a * (1.0 - band)
        previous = REGIME_UNKNOWN if i == 0 else out[i - 1]
        price = close[i]
        if price > upper:
            out[i] = REGIME_BULL
        elif price < lower:
            out[i] = REGIME_BEAR
        elif previous != REGIME_UNKNOWN:
            out[i] = previous
        elif price > a:
            out[i] = REGIME_BULL
        elif price < a:
            out[i] = REGIME_BEAR
        else:
            out[i] = REGIME_UNKNOWN
    return out
