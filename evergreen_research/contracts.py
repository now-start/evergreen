from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from evergreen_research.data import CandleBar


CONTRACT_SCHEMA_VERSION = 1


class SignalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class PositionSnapshot:
    qty: float = 0.0
    avg_price: float = 0.0
    updated_at: datetime | None = None
    position_ratio: float = 0.0

    @property
    def has_position(self) -> bool:
        return math.isfinite(self.qty) and self.qty > 0.0


@dataclass(frozen=True)
class StrategyInput:
    candles: list[CandleBar]
    signal_index: int
    position: PositionSnapshot
    params: dict[str, Any]


@dataclass(frozen=True)
class StrategyDiagnostic:
    key: str
    label: str
    value: float


@dataclass(frozen=True)
class StrategyDecision:
    action: SignalAction
    signal_reason: str
    target_position_ratio: float | None = None


@dataclass(frozen=True)
class StrategyEvaluation:
    decision: StrategyDecision
    diagnostics: list[StrategyDiagnostic]


def resolve_action(should_buy: bool, should_sell: bool) -> SignalAction:
    if should_buy and should_sell:
        raise ValueError("should_buy and should_sell cannot both be true")
    if should_buy:
        return SignalAction.BUY
    if should_sell:
        return SignalAction.SELL
    return SignalAction.HOLD


def resolve_target_position_ratio(
    action: SignalAction,
    current_position_ratio: float,
) -> float:
    if action == SignalAction.BUY:
        return 1.0
    if action == SignalAction.SELL:
        return 0.0
    return current_position_ratio


def reason_from_row(row: Any) -> str:
    return str(getattr(row, "signal_reason"))


def target_position_ratio_from_row(row: Any) -> float | None:
    value = float(getattr(row, "target_position_ratio"))
    return value if math.isfinite(value) else None


def diagnostics_from_row(row: Any) -> list[StrategyDiagnostic]:
    diagnostics: list[StrategyDiagnostic] = []
    raw_diagnostics = getattr(row, "diagnostics", {})
    if isinstance(raw_diagnostics, dict):
        for key, value in raw_diagnostics.items():
            if isinstance(value, bool):
                diagnostics.append(StrategyDiagnostic(key=str(key), label=str(key), value=1.0 if value else 0.0))
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                diagnostics.append(StrategyDiagnostic(key=str(key), label=str(key), value=float(value)))
    for key, value in vars(row).items():
        if key in {"timestamp", "action", "signal_reason", "diagnostics"}:
            continue
        if isinstance(value, bool):
            diagnostics.append(StrategyDiagnostic(key=key, label=key, value=1.0 if value else 0.0))
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            diagnostics.append(StrategyDiagnostic(key=key, label=key, value=float(value)))
    return diagnostics


def evaluation_from_row(row: Any) -> StrategyEvaluation:
    action = SignalAction(str(getattr(row, "action")))
    return StrategyEvaluation(
        decision=StrategyDecision(
            action=action,
            signal_reason=reason_from_row(row),
            target_position_ratio=target_position_ratio_from_row(row),
        ),
        diagnostics=diagnostics_from_row(row),
    )


def evaluation_to_dict(evaluation: StrategyEvaluation) -> dict[str, Any]:
    return {
        "decision": {
            "action": evaluation.decision.action.value,
            "signalReason": evaluation.decision.signal_reason,
            "targetPositionRatio": evaluation.decision.target_position_ratio,
        },
        "diagnostics": [
            {"key": item.key, "label": item.label, "value": item.value}
            for item in evaluation.diagnostics
        ],
    }


def strategy_io_contract() -> dict[str, Any]:
    return {
        "schemaVersion": CONTRACT_SCHEMA_VERSION,
        "input": {
            "type": "StrategyInput",
            "fields": {
                "candles": "OhlcvCandle[]",
                "signalIndex": "int",
                "position": {
                    "type": "PositionSnapshot",
                    "fields": {
                        "qty": "float",
                        "avgPrice": "float",
                        "updatedAt": "datetime | null",
                        "positionRatio": "float",
                    },
                },
                "params": "version-specific object",
            },
        },
        "output": {
            "type": "StrategyEvaluation",
            "fields": {
                "decision": {
                    "action": "BUY | SELL | HOLD",
                    "signalReason": "string",
                    "targetPositionRatio": "float | null",
                },
                "diagnostics": "StrategyDiagnostic[]",
            },
            "actionEnum": [item.value for item in SignalAction],
        },
        "javaTypes": {
            "input": "org.nowstart.evergreen.service.strategy.core.StrategyInput",
            "output": "org.nowstart.evergreen.service.strategy.core.StrategyEvaluation",
            "action": "org.nowstart.evergreen.service.strategy.core.SignalAction",
        },
    }
