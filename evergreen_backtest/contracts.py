from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from evergreen_backtest.data import CandleBar


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

    @property
    def buy_signal(self) -> bool:
        return self.action == SignalAction.BUY

    @property
    def sell_signal(self) -> bool:
        return self.action == SignalAction.SELL


@dataclass(frozen=True)
class StrategyEvaluation:
    decision: StrategyDecision
    diagnostics: list[StrategyDiagnostic]


def action_from_signals(buy_signal: bool, sell_signal: bool) -> SignalAction:
    if buy_signal and sell_signal:
        raise ValueError("buy_signal and sell_signal cannot both be true")
    if buy_signal:
        return SignalAction.BUY
    if sell_signal:
        return SignalAction.SELL
    return SignalAction.HOLD


def target_position_ratio_from_signals(
    buy_signal: bool,
    sell_signal: bool,
    current_position_ratio: float,
) -> float:
    action = action_from_signals(buy_signal, sell_signal)
    if action == SignalAction.BUY:
        return 1.0
    if action == SignalAction.SELL:
        return 0.0
    return current_position_ratio


def reason_from_row(row: Any) -> str:
    if hasattr(row, "signal_reason"):
        return str(getattr(row, "signal_reason"))

    action = SignalAction(str(getattr(row, "action", action_from_signals(row.buy_signal, row.sell_signal).value)))
    if action == SignalAction.BUY:
        if getattr(row, "setup_buy", False):
            return "SETUP_BUY"
        return "BUY_SIGNAL"
    if action == SignalAction.SELL:
        if getattr(row, "trail_stop_triggered", False):
            return "TRAIL_STOP"
        if getattr(row, "setup_sell", False):
            return "SETUP_SELL"
        return "SELL_SIGNAL"
    return "HOLD"


def target_position_ratio_from_row(row: Any) -> float | None:
    for attr in ("target_position_ratio", "target_exposure"):
        if hasattr(row, attr):
            value = float(getattr(row, attr))
            return value if math.isfinite(value) else None

    action = SignalAction(str(getattr(row, "action", action_from_signals(row.buy_signal, row.sell_signal).value)))
    if action == SignalAction.BUY:
        return 1.0
    if action == SignalAction.SELL:
        return 0.0

    value = float(getattr(row, "pos_open", 0.0))
    return value if math.isfinite(value) else None


def diagnostics_from_row(row: Any) -> list[StrategyDiagnostic]:
    diagnostics: list[StrategyDiagnostic] = []
    for key, value in vars(row).items():
        if key in {"timestamp", "action", "buy_signal", "sell_signal"}:
            continue
        if isinstance(value, bool):
            diagnostics.append(StrategyDiagnostic(key=key, label=key, value=1.0 if value else 0.0))
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            diagnostics.append(StrategyDiagnostic(key=key, label=key, value=float(value)))
    return diagnostics


def evaluation_from_row(row: Any) -> StrategyEvaluation:
    action = SignalAction(str(getattr(row, "action", action_from_signals(row.buy_signal, row.sell_signal).value)))
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
                "position": "PositionSnapshot",
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
