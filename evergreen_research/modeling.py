from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from evergreen_research.contracts import SignalAction
from evergreen_research.data import CandleBar


@dataclass(frozen=True)
class ModelSignal:
    timestamp: datetime
    open: float
    close: float
    action: SignalAction
    signal_reason: str
    target_position_ratio: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


class StrategyModel(Protocol):
    def evaluate(self, bars: list[CandleBar], params: Any) -> list[ModelSignal]:
        """Return model decisions only. No PnL, scoring, or candidate ranking."""


def parse_double_range(spec: str) -> list[float]:
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) != 3:
        raise ValueError(f"range must be start:end:step, got: {spec}")
    start, end, step = float(parts[0]), float(parts[1]), float(parts[2])
    if step <= 0:
        raise ValueError(f"range step must be > 0, got: {spec}")
    if end < start:
        raise ValueError(f"range end must be >= start, got: {spec}")

    out: list[float] = []
    value = start
    while value <= end + 1e-12:
        out.append(value)
        value += step
    return out


def parse_int_range(spec: str) -> list[int]:
    return [int(round(value)) for value in parse_double_range(spec)]


def parse_positive_int_range(spec: str, field_name: str) -> list[int]:
    values = parse_int_range(spec)
    for value in values:
        if value <= 0:
            raise ValueError(f"{field_name} values must be > 0")
    return values


def action_from_target(current_position_ratio: float, target_position_ratio: float) -> SignalAction:
    if target_position_ratio > current_position_ratio + 1e-12:
        return SignalAction.BUY
    if target_position_ratio < current_position_ratio - 1e-12:
        return SignalAction.SELL
    return SignalAction.HOLD
