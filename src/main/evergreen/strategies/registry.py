"""Strategy catalog and routing; no account, order, or training dependencies."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from evergreen.market import Candle
from evergreen.strategies import band, breakout, deep, hybrid, rsi
from evergreen.strategies.trend import sma_target
from evergreen.strategies.types import Side, Strategy

STRATEGY_PARAMETERS: dict[Strategy, dict[str, int | str]] = {
    "cash": {},
    "buy-hold": {},
    "sma-trend-v0": {"fast": 20, "slow": 60, "entry_gap": "0.002", "exit_gap": "0"},
    "sma-slow-v1": {"fast": 48, "slow": 168, "entry_gap": "0.002", "exit_gap": "0"},
    "breakout-v1": {"entry_lookback": 168, "exit_lookback": 48, "entry_buffer": "0.001"},
    "band-reversion-v1": {"lookback": 24, "std_multiple": 2, "ddof": 0},
    "rsi-rebound-v1": {
        "rsi_period": 14,
        "rsi_method": "simple",
        "entry_cross": 30,
        "exit_level": 55,
        "trend_period": 168,
    },
    "regime-rules-v1": {"up": "breakout-v1", "sideways": "band-reversion-v1", "down": "cash"},
    "regime-mlp-v1": {"up": "breakout-v1", "sideways": "band-reversion-v1", "down": "cash"},
}
ML_STRATEGIES: tuple[Strategy, ...] = ("mlp-v1", "cnn-v1")
HYBRID_COMPONENTS: dict[Strategy, tuple[Strategy, Strategy]] = {
    "mlp-trend-v1": ("mlp-v1", "sma-slow-v1"),
    "cnn-trend-v1": ("cnn-v1", "sma-slow-v1"),
    "mlp-breakout-v1": ("mlp-v1", "breakout-v1"),
    "cnn-breakout-v1": ("cnn-v1", "breakout-v1"),
    "mlp-rsi-v1": ("mlp-v1", "rsi-rebound-v1"),
    "cnn-rsi-v1": ("cnn-v1", "rsi-rebound-v1"),
}
TIMED_RULES: dict[Strategy, Strategy] = {
    "trend-12h-v1": "sma-slow-v1",
    "breakout-12h-v1": "breakout-v1",
    "rsi-12h-v1": "rsi-rebound-v1",
}
META_COMPONENTS: dict[Strategy, tuple[Strategy, Strategy]] = {
    "meta-mlp-trend-v1": ("mlp-v1", "sma-slow-v1"),
    "meta-cnn-trend-v1": ("cnn-v1", "sma-slow-v1"),
    "meta-mlp-breakout-v1": ("mlp-v1", "breakout-v1"),
    "meta-cnn-breakout-v1": ("cnn-v1", "breakout-v1"),
    "meta-mlp-rsi-v1": ("mlp-v1", "rsi-rebound-v1"),
    "meta-cnn-rsi-v1": ("cnn-v1", "rsi-rebound-v1"),
}
PREDICTIVE_STRATEGIES = (*ML_STRATEGIES, *HYBRID_COMPONENTS, *META_COMPONENTS)
for _model_name in ML_STRATEGIES:
    STRATEGY_PARAMETERS[_model_name] = {
        "lookback": 32,
        "horizon": 12,
        "entry_probability": "0.60",
        "exit_probability": "0.45",
    }
for _hybrid_name, (_model_name, _rule_name) in HYBRID_COMPONENTS.items():
    STRATEGY_PARAMETERS[_hybrid_name] = {
        **STRATEGY_PARAMETERS[_rule_name],
        **STRATEGY_PARAMETERS[_model_name],
        "model": _model_name,
        "rule": _rule_name,
        "entry_combination": "and",
        "exit_combination": "or",
    }
for _timed_name, _rule_name in TIMED_RULES.items():
    STRATEGY_PARAMETERS[_timed_name] = {
        **STRATEGY_PARAMETERS[_rule_name],
        "rule": _rule_name,
        "max_hold_hours": 12,
    }
for _meta_name, (_model_name, _rule_name) in META_COMPONENTS.items():
    STRATEGY_PARAMETERS[_meta_name] = {
        **STRATEGY_PARAMETERS[_rule_name],
        "rule": _rule_name,
        "model": _model_name,
        "entry_probability": "0.50",
        "max_hold_hours": 12,
        "exit": "rule_or_time_or_risk",
        "learning_task": "candidate_net_profit",
    }


def warmup_bars(strategy: Strategy) -> int:
    if strategy in ("regime-rules-v1", "regime-mlp-v1"):
        return 169
    if strategy in META_COMPONENTS:
        return max(55, warmup_bars(META_COMPONENTS[strategy][1]))
    if strategy in TIMED_RULES:
        return warmup_bars(TIMED_RULES[strategy])
    if strategy in HYBRID_COMPONENTS:
        return max(warmup_bars(part) for part in HYBRID_COMPONENTS[strategy])
    if strategy in ML_STRATEGIES:
        return 55
    if strategy in ("sma-slow-v1", "rsi-rebound-v1"):
        return 168
    if strategy == "band-reversion-v1":
        return 25
    if strategy == "breakout-v1":
        return 169
    return 60


def strategy_target(history: Sequence[Candle], holding: bool, strategy: Strategy) -> Side | None:
    if strategy in ("regime-rules-v1", "regime-mlp-v1"):
        raise ValueError("장세 전환에는 별도의 장세 판단과 진입 전략 상태가 필요합니다")
    if strategy in PREDICTIVE_STRATEGIES:
        raise ValueError("딥러닝 전략에는 별도의 예측 확률이 필요합니다")
    if len(history) < warmup_bars(strategy):
        return None
    if strategy == "band-reversion-v1":
        return band.target(history, holding)
    if strategy == "rsi-rebound-v1":
        return rsi.target(history, holding)
    if strategy == "breakout-v1":
        return breakout.target(history, holding)
    closes = [bar.close for bar in history[-168:]]
    if strategy == "sma-slow-v1":
        return sma_target(closes, holding, fast_period=48, slow_period=168)
    return sma_target(closes, holding)


def hybrid_target(
    history: Sequence[Candle], holding: bool, strategy: Strategy, probability: Decimal
) -> Side | None:
    _, rule = HYBRID_COMPONENTS[strategy]
    return hybrid.target(strategy_target(history, holding, rule), holding, probability)


def signal_target(
    history: Sequence[Candle],
    holding: bool,
    strategy: Strategy,
    *,
    probability: Decimal | None = None,
    entry_time: datetime | None = None,
) -> Side | None:
    if strategy in TIMED_RULES or strategy in META_COMPONENTS:
        rule = TIMED_RULES[strategy] if strategy in TIMED_RULES else META_COMPONENTS[strategy][1]
        if (
            holding
            and entry_time is not None
            and history[-1].close_time - entry_time >= timedelta(hours=12)
        ):
            return "sell"
        signal = strategy_target(history, holding, rule)
        if holding or strategy in TIMED_RULES:
            return signal
        if probability is None:
            raise ValueError("메타 모델의 예측이 필요합니다")
        return hybrid.meta_target(signal, holding, probability)
    if probability is not None:
        if strategy in HYBRID_COMPONENTS:
            return hybrid_target(history, holding, strategy, probability)
        return deep.target(holding, probability)
    return strategy_target(history, holding, strategy)
