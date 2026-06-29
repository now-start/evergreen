"""V6 model: 4-hour trend2 rule from the conference winner."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterator

from evergreen_backtest.data import CandleBar
from evergreen_backtest.modeling import ModelSignal, action_from_target, parse_double_range
from evergreen_backtest.models.indicators import moving_average


PREFERRED_INTERVAL_KEY = "minute_240"
JAVA_PARAM_FIELDS = {
    "ruleScale",
    "buyCutoff",
    "sellCutoff",
}


@dataclass(frozen=True)
class StrategyParamsV6:
    fee_per_side: float
    slippage: float
    rule_scale: float
    buy_cutoff: float
    sell_cutoff: float
    dl_enabled: bool
    dl_train_tail_rows: int
    dl_min_train_rows: int
    dl_epochs: int
    dl_profile: "DeepLearningProfileV6 | None" = field(
        default=None,
        compare=False,
        repr=False,
        metadata={"serialize": False},
    )


@dataclass(frozen=True)
class BacktestConfigV6:
    enabled: bool = True
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 5
    grid_parallelism: int = 1
    grid_rule_scale_range: str = "auto"
    grid_buy_cutoff_range: str = "0.055:0.055:1"
    grid_sell_cutoff_range: str = "0.25:0.25:1"
    dl_enabled: bool = True
    dl_train_tail_rows: int = 8000
    dl_min_train_rows: int = 1000
    dl_epochs: int = 130

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation_ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee_per_side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top_k must be > 0")
        if self.grid_parallelism <= 0:
            raise ValueError("grid_parallelism must be > 0")
        if self.dl_train_tail_rows <= 0 or self.dl_min_train_rows <= 0:
            raise ValueError("dl train row limits must be > 0")
        if self.dl_epochs <= 0:
            raise ValueError("dl_epochs must be > 0")


class StrategyModelV6:
    def evaluate(self, bars: list[CandleBar], params: StrategyParamsV6) -> list[ModelSignal]:
        if bars is None or len(bars) < 2:
            raise ValueError("At least 2 bars are required")
        _validate_params(params)

        close = [bar.close for bar in bars]
        trend = _trend2_components(close)
        rule_scale = _resolve_rule_scale(params.rule_scale, trend.trend2)
        dl_scores = _dl_signal_scores(params.dl_profile, bars, trend, rule_scale)
        target_close = [0.0] * len(bars)
        signals: list[ModelSignal] = []

        for i, bar in enumerate(bars):
            current_open = 0.0 if i == 0 else target_close[i - 1]
            trend2 = trend.trend2[i]
            rule_score = abs(trend2) / rule_scale if math.isfinite(trend2) else math.nan
            dl = dl_scores[i]
            setup_buy = trend2 > 0.0 and rule_score >= params.buy_cutoff
            setup_sell = trend2 < 0.0 and rule_score >= params.sell_cutoff and current_open > 0.0

            if not math.isfinite(rule_score):
                target = current_open
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
                        "trend2": trend2,
                        "trend2_score": rule_score,
                        "ema_component": trend.ema_component[i],
                        "ma_component": trend.ma_component[i],
                        "macd_component": trend.macd_component[i],
                        "momentum_component": trend.momentum_component[i],
                        "buy_cutoff": params.buy_cutoff,
                        "sell_cutoff": params.sell_cutoff,
                        "rule_scale": rule_scale,
                        "rule_scale_mode": "fixed" if math.isfinite(params.rule_scale) else "auto_q70_abs_trend2",
                        "dl_enabled": params.dl_enabled,
                        "dl_profile_ready": params.dl_profile is not None,
                        "dl_probability": dl.probability,
                        "dl_model_side": dl.model_side,
                        "dl_model_confidence": dl.confidence,
                        "dl_model_agreement": dl.agreement,
                        "dl_score_modifier": dl.score_modifier,
                        "contest_signal_score": dl.score_modifier,
                        "setup_buy": setup_buy,
                        "setup_sell": setup_sell,
                    },
                )
            )
        return signals


def iter_parameter_grid(config: BacktestConfigV6) -> Iterator[StrategyParamsV6]:
    if config.grid_rule_scale_range.strip().lower() != "auto":
        rule_scales = tuple(parse_double_range(config.grid_rule_scale_range))
    else:
        rule_scales = (math.nan,)
    for rule_scale in rule_scales:
        for buy_cutoff in parse_double_range(config.grid_buy_cutoff_range):
            for sell_cutoff in parse_double_range(config.grid_sell_cutoff_range):
                yield StrategyParamsV6(
                    fee_per_side=config.fee_per_side,
                    slippage=config.slippage,
                    rule_scale=rule_scale,
                    buy_cutoff=buy_cutoff,
                    sell_cutoff=sell_cutoff,
                    dl_enabled=config.dl_enabled,
                    dl_train_tail_rows=config.dl_train_tail_rows,
                    dl_min_train_rows=config.dl_min_train_rows,
                    dl_epochs=config.dl_epochs,
                )


def resolve_selected_params(bars: list[CandleBar], params: StrategyParamsV6) -> StrategyParamsV6:
    trend = _trend2_components([bar.close for bar in bars])
    scale_values = _tail_values(trend.trend2, params.dl_train_tail_rows)
    rule_scale = params.rule_scale if math.isfinite(params.rule_scale) else _resolve_rule_scale(params.rule_scale, scale_values)
    dl_profile = _fit_deep_learning_profile(bars, trend, rule_scale, params) if params.dl_enabled else None
    return StrategyParamsV6(
        fee_per_side=params.fee_per_side,
        slippage=params.slippage,
        rule_scale=rule_scale,
        buy_cutoff=params.buy_cutoff,
        sell_cutoff=params.sell_cutoff,
        dl_enabled=params.dl_enabled,
        dl_train_tail_rows=params.dl_train_tail_rows,
        dl_min_train_rows=params.dl_min_train_rows,
        dl_epochs=params.dl_epochs,
        dl_profile=dl_profile,
    )


@dataclass(frozen=True)
class Trend2Components:
    ema_component: list[float]
    ma_component: list[float]
    macd_component: list[float]
    momentum_component: list[float]
    trend2: list[float]


@dataclass(frozen=True)
class DeepLearningProfileV6:
    feature_names: tuple[str, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]
    w1: tuple[tuple[float, ...], ...]
    b1: tuple[float, ...]
    w2: tuple[tuple[float, ...], ...]
    b2: tuple[float, ...]
    w3: tuple[float, ...]
    b3: float


@dataclass(frozen=True)
class DeepLearningScoreV6:
    probability: float
    model_side: int
    confidence: float
    agreement: float
    score_modifier: float


DL_FEATURE_NAMES = (
    "candle_return",
    "range_pct",
    "volume_change_1",
    "return_lag_1",
    "return_lag_2",
    "return_lag_3",
    "return_lag_5",
    "return_lag_10",
    "ema_component",
    "ma_component",
    "macd_component",
    "momentum_component",
    "trend2",
    "trend2_signed_score",
)


def _trend2_components(close: list[float]) -> Trend2Components:
    ema12 = _ewm_adjust_false_min_periods(close, 12)
    ema26 = _ewm_adjust_false_min_periods(close, 26)
    ma10 = moving_average(close, 10)
    ma20 = moving_average(close, 20)
    macd_line, macd_signal = _macd(close)
    ret5 = _return_lag(close, 5)
    ret10 = _return_lag(close, 10)

    ema_component: list[float] = []
    ma_component: list[float] = []
    macd_component: list[float] = []
    momentum_component: list[float] = []
    trend2: list[float] = []
    for i, price in enumerate(close):
        if price <= 0.0 or not math.isfinite(price):
            ema_component.append(math.nan)
            ma_component.append(math.nan)
            macd_component.append(math.nan)
            momentum_component.append(math.nan)
            trend2.append(math.nan)
            continue
        ema_value = _gap(price, ema12[i]) + (0.7 * _gap(price, ema26[i]))
        ma_value = _gap(price, ma10[i]) + _gap(price, ma20[i])
        macd_value = _safe(macd_line[i]) + _safe(macd_signal[i])
        momentum_value = 0.5 * (_safe(ret5[i]) + _safe(ret10[i]))
        ema_component.append(ema_value)
        ma_component.append(ma_value)
        macd_component.append(macd_value)
        momentum_component.append(momentum_value)
        trend2.append(ema_value + ma_value + macd_value + momentum_value)
    return Trend2Components(
        ema_component=ema_component,
        ma_component=ma_component,
        macd_component=macd_component,
        momentum_component=momentum_component,
        trend2=trend2,
    )


def _fit_deep_learning_profile(
    bars: list[CandleBar],
    trend: Trend2Components,
    rule_scale: float,
    params: StrategyParamsV6,
) -> DeepLearningProfileV6 | None:
    try:
        import numpy as np
    except ImportError:
        return None

    rows: list[list[float]] = []
    targets: list[float] = []
    for i in range(len(bars) - 1):
        current = bars[i].close
        next_close = bars[i + 1].close
        if not (math.isfinite(current) and math.isfinite(next_close) and current > 0.0):
            continue
        rows.append(_dl_feature_row(bars, trend, rule_scale, i))
        targets.append(1.0 if (next_close / current) - 1.0 > 0.0 else 0.0)

    if len(rows) > params.dl_train_tail_rows:
        rows = rows[-params.dl_train_tail_rows :]
        targets = targets[-params.dl_train_tail_rows :]
    if len(rows) < params.dl_min_train_rows:
        return None

    x_raw = np.asarray(rows, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64).reshape(-1, 1)
    center = np.nanmedian(x_raw, axis=0)
    q75 = np.nanpercentile(x_raw, 75, axis=0)
    q25 = np.nanpercentile(x_raw, 25, axis=0)
    scale = q75 - q25
    std = np.nanstd(x_raw, axis=0)
    scale = np.where(np.isfinite(scale) & (np.abs(scale) >= 1e-8), scale, std)
    scale = np.where(np.isfinite(scale) & (np.abs(scale) >= 1e-8), scale, 1.0)
    center = np.where(np.isfinite(center), center, 0.0)
    x = np.clip(np.nan_to_num((x_raw - center) / scale, nan=0.0, posinf=8.0, neginf=-8.0), -8.0, 8.0)

    rng = np.random.default_rng(5005)
    input_dim = x.shape[1]
    hidden1 = 32
    hidden2 = 12
    w1 = rng.normal(0.0, math.sqrt(2.0 / input_dim), size=(input_dim, hidden1))
    b1 = np.zeros((hidden1,), dtype=np.float64)
    w2 = rng.normal(0.0, math.sqrt(2.0 / hidden1), size=(hidden1, hidden2))
    b2 = np.zeros((hidden2,), dtype=np.float64)
    w3 = rng.normal(0.0, math.sqrt(2.0 / hidden2), size=(hidden2, 1))
    b3 = np.zeros((1,), dtype=np.float64)
    _train_mlp(
        x=x,
        y=y,
        epochs=params.dl_epochs,
        rng=rng,
        weights=[w1, w2, w3],
        biases=[b1, b2, b3],
    )
    return DeepLearningProfileV6(
        feature_names=DL_FEATURE_NAMES,
        center=tuple(float(value) for value in center),
        scale=tuple(float(value) for value in scale),
        w1=tuple(tuple(float(item) for item in row) for row in w1),
        b1=tuple(float(value) for value in b1),
        w2=tuple(tuple(float(item) for item in row) for row in w2),
        b2=tuple(float(value) for value in b2),
        w3=tuple(float(row[0]) for row in w3),
        b3=float(b3[0]),
    )


def _train_mlp(
    *,
    x: object,
    y: object,
    epochs: int,
    rng: object,
    weights: list[object],
    biases: list[object],
) -> None:
    import numpy as np

    w1, w2, w3 = weights
    b1, b2, b3 = biases
    learning_rate = 0.005
    weight_decay = 0.01
    dropout_rate = 0.03
    keep_probability = 1.0 - dropout_rate
    beta1 = 0.9
    beta2 = 0.999
    epsilon = 1e-8
    pos_rate = float(np.mean(y))
    pos_weight = (1.0 - pos_rate) / max(pos_rate, 1e-3)

    params = [w1, b1, w2, b2, w3, b3]
    first_moment = [np.zeros_like(param) for param in params]
    second_moment = [np.zeros_like(param) for param in params]

    for epoch in range(1, epochs + 1):
        z1 = x @ w1 + b1
        a1 = np.maximum(z1, 0.0)
        dropout_mask = (rng.random(a1.shape) < keep_probability).astype(np.float64) / keep_probability
        a1_drop = a1 * dropout_mask
        z2 = a1_drop @ w2 + b2
        a2 = np.maximum(z2, 0.0)
        logits = a2 @ w3 + b3
        probability = 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))
        sample_weight = np.where(y > 0.5, pos_weight, 1.0)
        dlogits = ((probability - y) * sample_weight) / float(len(y))

        grad_w3 = a2.T @ dlogits
        grad_b3 = np.sum(dlogits, axis=0)
        da2 = dlogits @ w3.T
        dz2 = da2 * (z2 > 0.0)
        grad_w2 = a1_drop.T @ dz2
        grad_b2 = np.sum(dz2, axis=0)
        da1_drop = dz2 @ w2.T
        da1 = da1_drop * dropout_mask
        dz1 = da1 * (z1 > 0.0)
        grad_w1 = x.T @ dz1
        grad_b1 = np.sum(dz1, axis=0)

        gradients = [grad_w1, grad_b1, grad_w2, grad_b2, grad_w3, grad_b3]
        for index, (param, grad) in enumerate(zip(params, gradients, strict=True)):
            first_moment[index] = (beta1 * first_moment[index]) + ((1.0 - beta1) * grad)
            second_moment[index] = (beta2 * second_moment[index]) + ((1.0 - beta2) * (grad * grad))
            m_hat = first_moment[index] / (1.0 - beta1**epoch)
            v_hat = second_moment[index] / (1.0 - beta2**epoch)
            if param.ndim == 2:
                param *= 1.0 - (learning_rate * weight_decay)
            param -= learning_rate * m_hat / (np.sqrt(v_hat) + epsilon)


def _dl_signal_scores(
    profile: DeepLearningProfileV6 | None,
    bars: list[CandleBar],
    trend: Trend2Components,
    rule_scale: float,
) -> list[DeepLearningScoreV6]:
    if profile is None:
        return [DeepLearningScoreV6(math.nan, 0, math.nan, 1.0, 1.0) for _ in bars]

    import numpy as np

    rows = [_dl_feature_row(bars, trend, rule_scale, i) for i in range(len(bars))]
    x_raw = np.asarray(rows, dtype=np.float64)
    center = np.asarray(profile.center, dtype=np.float64)
    scale = np.asarray(profile.scale, dtype=np.float64)
    x = np.clip(np.nan_to_num((x_raw - center) / scale, nan=0.0, posinf=8.0, neginf=-8.0), -8.0, 8.0)
    w1 = np.asarray(profile.w1, dtype=np.float64)
    b1 = np.asarray(profile.b1, dtype=np.float64)
    w2 = np.asarray(profile.w2, dtype=np.float64)
    b2 = np.asarray(profile.b2, dtype=np.float64)
    w3 = np.asarray(profile.w3, dtype=np.float64).reshape(-1, 1)
    b3 = np.asarray([profile.b3], dtype=np.float64)
    a1 = np.maximum(x @ w1 + b1, 0.0)
    a2 = np.maximum(a1 @ w2 + b2, 0.0)
    logits = a2 @ w3 + b3
    probabilities = (1.0 / (1.0 + np.exp(-np.clip(logits.reshape(-1), -50.0, 50.0)))).tolist()

    scores: list[DeepLearningScoreV6] = []
    for i, probability in enumerate(probabilities):
        if not math.isfinite(probability):
            scores.append(DeepLearningScoreV6(math.nan, 0, math.nan, 1.0, 1.0))
            continue
        model_side = 1 if probability >= 0.5 else -1
        rule_side = 1 if trend.trend2[i] > 0.0 else (-1 if trend.trend2[i] < 0.0 else 0)
        confidence = abs(probability - 0.5) * 2.0
        agreement = 1.0 if model_side == rule_side else 0.94
        score_modifier = agreement * (1.0 + (0.05 * confidence))
        scores.append(DeepLearningScoreV6(probability, model_side, confidence, agreement, score_modifier))
    return scores


def _dl_feature_row(
    bars: list[CandleBar],
    trend: Trend2Components,
    rule_scale: float,
    index: int,
) -> list[float]:
    bar = bars[index]
    close = [item.close for item in bars]
    previous_volume = bars[index - 1].volume if index > 0 else math.nan
    trend2 = trend.trend2[index]
    return [
        _gap(bar.close, bar.open),
        ((bar.high - bar.low) / bar.close) if math.isfinite(bar.close) and bar.close > 0.0 else 0.0,
        _gap(bar.volume, previous_volume),
        _return_lag_at(close, index, 1),
        _return_lag_at(close, index, 2),
        _return_lag_at(close, index, 3),
        _return_lag_at(close, index, 5),
        _return_lag_at(close, index, 10),
        _safe(trend.ema_component[index]),
        _safe(trend.ma_component[index]),
        _safe(trend.macd_component[index]),
        _safe(trend.momentum_component[index]),
        _safe(trend2),
        trend2 / rule_scale if math.isfinite(trend2) and rule_scale > 0.0 else 0.0,
    ]


def _macd(close: list[float]) -> tuple[list[float], list[float]]:
    ema12 = _ewm_adjust_false_min_periods(close, 12)
    ema26 = _ewm_adjust_false_min_periods(close, 26)
    raw_line = [
        ema12[i] - ema26[i]
        if math.isfinite(ema12[i]) and math.isfinite(ema26[i])
        else math.nan
        for i in range(len(close))
    ]
    signal_raw = _ewm_adjust_false_min_periods(raw_line, 9)
    macd_line = [
        raw_line[i] / close[i]
        if math.isfinite(raw_line[i]) and math.isfinite(close[i]) and close[i] > 0.0
        else math.nan
        for i in range(len(close))
    ]
    signal = [
        signal_raw[i] / close[i]
        if math.isfinite(signal_raw[i]) and math.isfinite(close[i]) and close[i] > 0.0
        else math.nan
        for i in range(len(close))
    ]
    return macd_line, signal


def _ewm_adjust_false_min_periods(values: list[float], length: int) -> list[float]:
    out = [math.nan] * len(values)
    if length <= 0:
        return out

    alpha = 2.0 / (length + 1.0)
    ema = math.nan
    finite_count = 0
    for i, value in enumerate(values):
        if not math.isfinite(value):
            continue
        ema = value if not math.isfinite(ema) else (alpha * value) + ((1.0 - alpha) * ema)
        finite_count += 1
        if finite_count >= length:
            out[i] = ema
    return out


def _return_lag(close: list[float], lag: int) -> list[float]:
    out = [math.nan] * len(close)
    for i in range(lag, len(close)):
        previous = close[i - lag]
        current = close[i]
        if previous > 0.0 and math.isfinite(previous) and math.isfinite(current):
            out[i] = (current / previous) - 1.0
    return out


def _return_lag_at(close: list[float], index: int, lag: int) -> float:
    if index < lag:
        return math.nan
    previous = close[index - lag]
    current = close[index]
    if previous > 0.0 and math.isfinite(previous) and math.isfinite(current):
        return (current / previous) - 1.0
    return math.nan


def _gap(current: float, anchor: float) -> float:
    if not math.isfinite(current) or not math.isfinite(anchor) or anchor <= 0.0:
        return 0.0
    return (current / anchor) - 1.0


def _safe(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def _resolve_rule_scale(raw_rule_scale: float, trend2: list[float]) -> float:
    if math.isfinite(raw_rule_scale) and raw_rule_scale > 0.0:
        return raw_rule_scale

    values = sorted(abs(value) for value in trend2 if math.isfinite(value) and abs(value) > 0.0)
    if not values:
        return 1.0
    scale = _quantile_linear(values, 0.70)
    if math.isfinite(scale) and scale > 1e-12:
        return scale
    midpoint = values[len(values) // 2]
    return midpoint if math.isfinite(midpoint) and midpoint > 1e-12 else 1.0


def _tail_values(values: list[float], limit: int) -> list[float]:
    finite = [value for value in values if math.isfinite(value)]
    return finite[-limit:] if len(finite) > limit else finite


def _quantile_linear(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    weight = position - lower
    return (values[lower] * (1.0 - weight)) + (values[upper] * weight)


def _validate_params(params: StrategyParamsV6) -> None:
    if params.fee_per_side < 0 or params.slippage < 0:
        raise ValueError("fee/slippage must be >= 0")
    if params.buy_cutoff < 0.0 or params.sell_cutoff < 0.0:
        raise ValueError("cutoffs must be >= 0")
    if math.isfinite(params.rule_scale) and params.rule_scale <= 0.0:
        raise ValueError("rule_scale must be > 0 when provided")


def _resolve_signal_reason(action: str, setup_buy: bool, setup_sell: bool) -> str:
    if action == "BUY":
        return "BUY_TREND2_POSITIVE"
    if action == "SELL":
        return "SELL_TREND2_NEGATIVE"
    if setup_buy:
        return "SETUP_BUY_TREND2"
    if setup_sell:
        return "SETUP_SELL_TREND2"
    return "NONE"
