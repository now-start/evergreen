"""Allowlisted chart evidence; never evaluate a strategy or send an exchange request."""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from evergreen.market import Candle
from evergreen.strategies.types import Side
from evergreen.trading.upbit import Order

if TYPE_CHECKING:
    from evergreen.trading.state import State

logger = logging.getLogger(__name__)


def bar_snapshot(
    state: "State", history: Sequence[Candle], signal: Side | None
) -> dict[str, object]:
    """Copy already-observed state. Decimal strings retain precision in MariaDB and Loki."""
    bar, buffer = history[-1], state.buffer
    protection = buffer.protection if buffer else None
    active = bool(protection and protection.peak >= protection.reference + 3 * protection.entry_tr)
    holding = protection is not None
    return {
        "schema_version": 1,
        "event": "strategy_bar",
        "event_id": f"{state.strategy}:bar:{bar.close_time.isoformat()}",
        "market": "KRW-BTC",
        "strategy": state.strategy,
        "mode": "live",
        "event_time": bar.close_time.timestamp(),
        "bar_close_time": bar.close_time.isoformat(),
        "timeframe": "1h",
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "entry_threshold": str(max(b.high for b in history[-169:-1]) * Decimal("1.001")),
        "initial_stop": str(protection.reference - 6 * protection.entry_tr) if protection else None,
        "trailing_stop": str(protection.threshold) if protection and active else None,
        "trailing_active": active,
        "channel_exit_threshold": str(min(b.low for b in history[-97:-1])) if holding else None,
        "reset_threshold": str(min(b.low for b in history[-49:-1])),
        "position": "btc" if holding else "cash",
        "candidate_signal": signal,
        "breach_count": protection.breaches if protection else 0,
        "rising_grace": (
            sum(b.close for b in history[-25:-1]) > sum(b.close for b in history[-49:-25])
            if holding
            else False
        ),
        "awaiting_reset": buffer.awaiting_reset if buffer else False,
        "exit_due": buffer.exit_due if buffer else False,
        "exit_reserved": state.reserved_exit is not None,
        "halted": state.halted,
    }


def execution_snapshot(state: "State", order: Order, now: datetime) -> list[dict[str, object]]:
    """One VWAP marker per reconciled terminal order, not an invented individual fill."""
    if order.executed_volume == 0 or not order.trades:
        return []
    times = [fill.created_at for fill in order.trades]
    complete_times = [time.astimezone(UTC) for time in times if time is not None]
    # Legacy orders can lack fill timestamps. Never substitute observation time on the chart.
    timed = len(complete_times) == len(times)
    last = max(complete_times) if timed else None
    funds = sum((fill.funds for fill in order.trades), Decimal(0))
    return [
        {
            "schema_version": 1,
            "event": "order_execution",
            "event_id": f"{state.strategy}:execution:{state.order_sequence}",
            "market": "KRW-BTC",
            "strategy": state.strategy,
            "mode": "live",
            "observed_at": now.astimezone(UTC).isoformat(),
            "event_time": last.timestamp() if last else None,
            "filled_at": last.isoformat() if last else None,
            "first_filled_at": min(complete_times).isoformat() if timed else None,
            "signal_time": state.intent_context.signal_time.isoformat()
            if state.intent_context
            else state.last_signal,
            "reason": state.intent_context.reason if state.intent_context else None,
            "order_sequence": state.order_sequence,
            "side": "buy" if order.side == "bid" else "sell",
            "terminal_state": order.state,
            "aggregation": "terminal_order_vwap",
            "fill_count": len(order.trades),
            "fill_price": str(funds / order.executed_volume),
            "quantity": str(order.executed_volume),
            "fee_amount": str(order.paid_fee),
            "fee_currency": "KRW",
            "remaining_btc": str(state.btc),
        }
    ]


def emit(snapshots: Sequence[dict[str, object]]) -> None:
    """Call only AFTER the transaction storing these exact snapshots succeeds."""
    for snapshot in snapshots:
        logger.info("%s", json.dumps(snapshot, separators=(",", ":"), allow_nan=False))
