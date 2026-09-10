"""Boundary adapter for the shared early3 state and durable exchange intents."""

from datetime import UTC, datetime
from decimal import Decimal

from evergreen.market import Candle
from evergreen.strategies.buffer import LIMIT, IntentContext, prior_mean_true_range, reset_required
from evergreen.strategies.types import Side
from evergreen.trading.state import State, StateUnavailable
from evergreen.trading.upbit import Chance, Order

STEP = Decimal(".00000001")


def signal(
    state: State, candles: list[Candle], chance: Chance, slippage: Decimal, *, prior_peak: Decimal
) -> tuple[Side | None, IntentContext | None]:
    buffer = state.buffer
    if buffer is None or state.krw is None or state.btc is None:
        raise StateUnavailable("buffer_state_missing")
    holding = buffer.protection is not None
    if (not holding and state.btc > STEP) or (holding and state.btc <= STEP):
        raise StateUnavailable("buffer_position_mismatch")
    if holding and buffer.position_since is None:
        raise StateUnavailable("buffer_entry_time_missing")
    end = candles[-1].close_time
    if buffer.last_bar is None:
        if holding:
            raise StateUnavailable("buffer_cursor_missing")
        prefixes = [candles]
    else:
        if buffer.last_bar > end:
            raise StateUnavailable("buffer_future_cursor")
        prefixes = [
            candles[: i + 1] for i, b in enumerate(candles) if b.close_time > buffer.last_bar
        ]
    for history in prefixes:
        if len(history) < 169:
            raise StateUnavailable("buffer_history_gap")
        if holding:
            bar = history[-1]
            # The entry-hour open predates a real intrabar fill; that price was not owned.
            prices = (
                (bar.open, bar.close)
                if buffer.position_since is not None and bar.open_time >= buffer.position_since
                else (bar.close,)
            )
            for price in prices:
                equity = state.krw + state.btc * price
                prior_peak = max(prior_peak, equity)
                state.halted = state.halted or equity <= prior_peak * (1 - LIMIT)
        buffer.observe(
            history,
            state.krw,
            state.btc if holding else Decimal(0),
            prior_peak,
            chance.ask_fee,
            slippage,
            halted=state.halted,
        )
        observed = buffer.signal(history, holding)
        if holding and state.halted:
            state.reserved_exit = IntentContext(signal_time=end, reason="risk")
        elif observed == "sell" and state.reserved_exit is None:
            state.reserved_exit = IntentContext(
                signal_time=history[-1].close_time,
                reset_after_sell=reset_required(history),
                reason="signal",
            )
    state.peak = max(state.peak, prior_peak)
    if state.reserved_exit is not None:
        if not holding:
            raise StateUnavailable("buffer_reserved_exit_without_position")
        return "sell", state.reserved_exit
    side = buffer.signal(candles, holding)
    if side is None:
        return None, None
    context = IntentContext(
        signal_time=end,
        entry_tr=prior_mean_true_range(candles),
        reset_after_sell=side == "sell" and reset_required(candles),
        reason="signal",
    )
    if side == "sell":
        state.reserved_exit = context
    return side, context


def settled(state: State, order: Order, now: datetime) -> None:
    """Called only after terminal fills and real balances were reconciled."""
    context, buffer = state.intent_context, state.buffer
    if context is None or buffer is None or state.btc is None or order.trades is None:
        raise StateUnavailable("buffer_intent_missing")
    if order.executed_volume == 0:
        return
    timestamps = [f.created_at for f in order.trades]
    if any(t is None for t in timestamps):
        raise StateUnavailable("buffer_fill_time_missing")
    times = [t.astimezone(UTC) for t in timestamps if t is not None]
    if any(t < context.signal_time or t > now for t in times):
        raise StateUnavailable("buffer_fill_time_invalid")
    hours = {t.replace(minute=0, second=0, microsecond=0) for t in times}
    if len(hours) != 1:
        raise StateUnavailable("buffer_fill_spans_hours")
    filled_hour = hours.pop()
    if order.side == "bid":
        if buffer.protection is not None:
            raise StateUnavailable("buffer_existing_position")
        funds = sum((f.funds for f in order.trades), Decimal(0))
        buffer.bought(funds / order.executed_volume, context.entry_tr)
        buffer.last_bar = filled_hour
        buffer.position_since = max(times)
    elif state.btc <= STEP:
        buffer.sold(reset=context.reason == "signal" and context.reset_after_sell)
        buffer.last_bar = filled_hour
        state.reserved_exit = None
    else:
        # A terminal partial sell is confirmed, but the exit reservation still owns the remainder.
        buffer.exit_due = True
        state.reserved_exit = context
