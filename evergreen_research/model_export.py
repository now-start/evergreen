"""Export a trained v6 model bundle (rule params + MLP weights) to JSON.

Training stays in Python (numpy RNG/dropout/quantile reductions are not
bit-reproducible in Java); the Java live engine only runs the forward pass.
This module serializes the trained ``DeepLearningProfileV6`` plus the resolved
rule params so the Java ``V6StrategyEngine`` can load identical weights and
reproduce signals.

It can also emit a self-contained golden fixture (candles + Python's expected
per-bar signals) for the Java parity test.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evergreen_research.contracts import CONTRACT_SCHEMA_VERSION
from evergreen_research.data import CandleBar
from evergreen_research.modeling import parse_double_range
from evergreen_research.models.v6 import (
    PREFERRED_INTERVAL_KEY,
    BacktestConfigV6,
    DeepLearningProfileV6,
    StrategyModelV6,
    StrategyParamsV6,
    resolve_selected_params,
)


def build_synthetic_bars(count: int, *, seed_price: float = 30_000_000.0) -> list[CandleBar]:
    """Deterministic 4-hour candle series (no numpy RNG) for reproducible fixtures."""
    if count <= 0:
        raise ValueError("count must be > 0")
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars: list[CandleBar] = []
    price = seed_price
    state = 123_456_789
    for i in range(count):
        state = (1_103_515_245 * state + 12_345) & 0x7FFFFFFF
        noise = (state / 0x7FFFFFFF) - 0.5
        drift = math.sin(i / 18.0) * 0.012
        ret = drift + (noise * 0.02)
        open_price = price
        close_price = max(1.0, price * (1.0 + ret))
        high = max(open_price, close_price) * (1.0 + (abs(noise) * 0.01))
        low = min(open_price, close_price) * (1.0 - (abs(noise) * 0.01))
        volume = 100.0 + float(state % 1000)
        bars.append(
            CandleBar(
                timestamp=start + timedelta(hours=4 * i),
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=volume,
            )
        )
        price = close_price
    return bars


def _first_range_value(spec: str) -> float:
    values = parse_double_range(spec)
    if not values:
        raise ValueError(f"grid range spec produced no values: {spec!r}")
    return values[0]


def _base_params(config: BacktestConfigV6) -> StrategyParamsV6:
    rule_scale_spec = config.grid_rule_scale_range.strip().lower()
    rule_scale = math.nan if rule_scale_spec == "auto" else _first_range_value(config.grid_rule_scale_range)
    return StrategyParamsV6(
        fee_per_side=config.fee_per_side,
        slippage=config.slippage,
        rule_scale=rule_scale,
        buy_cutoff=_first_range_value(config.grid_buy_cutoff_range),
        sell_cutoff=_first_range_value(config.grid_sell_cutoff_range),
        dl_enabled=config.dl_enabled,
        dl_train_tail_rows=config.dl_train_tail_rows,
        dl_min_train_rows=config.dl_min_train_rows,
        dl_epochs=config.dl_epochs,
    )


def _profile_to_dict(profile: DeepLearningProfileV6) -> dict[str, Any]:
    return {
        "enabled": True,
        "featureNames": list(profile.feature_names),
        "inputDim": len(profile.center),
        "hidden1": len(profile.b1),
        "hidden2": len(profile.b2),
        "center": [float(value) for value in profile.center],
        "scale": [float(value) for value in profile.scale],
        "w1": [[float(item) for item in row] for row in profile.w1],
        "b1": [float(value) for value in profile.b1],
        "w2": [[float(item) for item in row] for row in profile.w2],
        "b2": [float(value) for value in profile.b2],
        "w3": [float(value) for value in profile.w3],
        "b3": float(profile.b3),
    }


def _bundle_dict(selected: StrategyParamsV6) -> dict[str, Any]:
    deep_learning: dict[str, Any]
    if selected.dl_profile is None:
        deep_learning = {"enabled": False}
    else:
        deep_learning = _profile_to_dict(selected.dl_profile)
    return {
        "version": "v6",
        "contractSchemaVersion": CONTRACT_SCHEMA_VERSION,
        "intervalKey": PREFERRED_INTERVAL_KEY,
        "params": {
            "ruleScale": float(selected.rule_scale),
            "buyCutoff": float(selected.buy_cutoff),
            "sellCutoff": float(selected.sell_cutoff),
        },
        "deepLearning": deep_learning,
    }


def _json_number(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _golden_dict(selected: StrategyParamsV6, bars: list[CandleBar]) -> dict[str, Any]:
    signals = StrategyModelV6().evaluate(bars, selected)
    expected: list[dict[str, Any]] = []
    previous_target = 0.0
    for index, signal in enumerate(signals):
        diagnostics = signal.diagnostics
        expected.append(
            {
                "index": index,
                "timestamp": bars[index].timestamp.isoformat(),
                "action": signal.action.value,
                "signalReason": signal.signal_reason,
                "targetPositionRatio": _json_number(signal.target_position_ratio),
                "currentOpen": previous_target,
                "ruleScore": _json_number(float(diagnostics.get("trend2_score", math.nan))),
                "effectiveScore": _json_number(float(diagnostics.get("effective_score", math.nan))),
                "dlProbability": _json_number(float(diagnostics.get("dl_probability", math.nan))),
                "dlScoreModifier": _json_number(float(diagnostics.get("dl_score_modifier", math.nan))),
                "trend2": _json_number(float(diagnostics.get("trend2", math.nan))),
            }
        )
        previous_target = signal.target_position_ratio
    bundle = _bundle_dict(selected)
    bundle["candles"] = [
        {
            "timestamp": bar.timestamp.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    bundle["expectedSignals"] = expected
    return bundle


def export_v6(
    out_path: str | Path,
    *,
    golden_path: str | Path | None = None,
    bars: list[CandleBar] | None = None,
    synthetic_count: int = 1500,
    config_overrides: dict[str, Any] | None = None,
) -> StrategyParamsV6:
    """Train a v6 profile and write the production bundle (and optional golden fixture).

    Returns the resolved ``StrategyParamsV6`` (with the trained ``dl_profile``).
    """
    config = BacktestConfigV6(**(config_overrides or {}))
    candle_bars = bars if bars is not None else build_synthetic_bars(synthetic_count)
    selected = resolve_selected_params(candle_bars, _base_params(config))
    if not math.isfinite(selected.rule_scale):
        raise ValueError(f"resolved rule_scale is not finite, refusing to export: {selected.rule_scale}")

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(_bundle_dict(selected), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    if golden_path is not None:
        golden_file = Path(golden_path)
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(
            json.dumps(_golden_dict(selected, candle_bars), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    return selected
