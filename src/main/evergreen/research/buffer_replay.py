"""Event replay of the production early3 state machine using research execution costs."""

from datetime import datetime
from decimal import Decimal

from evergreen.market import Candle, utc_hour, validate_candles
from evergreen.research.backtest import Costs, Reason, Rejection, Result, _Portfolio
from evergreen.strategies.buffer import (
    LIMIT,
    BufferState,
    entry_budget,
    prior_mean_true_range,
    reset_required,
)
from evergreen.strategies.types import Side


def replay(
    candles: list[Candle], start: datetime, capital: Decimal, costs: Costs, delay: int = 0
) -> Result:
    start = utc_hour(start)
    if not candles or delay not in (0, 1) or not capital.is_finite() or capital <= 0:
        raise ValueError("잘못된 후보 재현 입력")
    bars, quality = validate_candles(
        candles, min(b.open_time for b in candles), max(b.close_time for b in candles)
    )
    if not quality.valid or quality.duplicates or quality.incomplete:
        raise ValueError("후보 재현 봉 품질 실패")
    indices = {b.open_time: i for i, b in enumerate(bars)}
    if start not in indices or indices[start] < 200:
        raise ValueError("후보 재현은 연속200봉 준비와 일치하는 시작 시각이 필요합니다")
    first = indices[start]
    portfolio = _Portfolio(capital, costs, True, drawdown_limit=LIMIT)
    state = BufferState()
    history = bars[:first]
    pending: tuple[int, Side, Reason, datetime] | None = None
    initial = state.signal(history, False)
    if initial:
        pending = (first + delay, initial, "signal", history[-1].close_time)
    exposure = 0
    for i in range(first, len(bars)):
        bar = bars[i]
        if pending and pending[0] == i:
            _, side, reason, time = pending
            signal_history = bars[: indices[time]]
            tr = prior_mean_true_range(signal_history)
            before = len(portfolio.fills)
            fits = side != "buy" or entry_budget(
                portfolio.cash,
                portfolio.peak,
                bar.open,
                tr,
                costs.buy_fee,
                costs.sell_fee,
                costs.buy_slippage,
                costs.sell_slippage,
                costs.quantity_step,
            )
            if fits:
                portfolio.trade(side, bar.open, bar.open_time, reason, time)
            else:
                portfolio.rejections.append(
                    Rejection(time=bar.open_time, side=side, reason="risk_budget", trigger=reason)
                )
            if len(portfolio.fills) > before:
                if side == "buy":
                    state.bought(bar.open, tr)
                else:
                    state.sold(reset=reason == "signal" and reset_required(signal_history))
            pending = None
        portfolio.mark(bar.open, bar.open_time, "open")
        exposure += int(portfolio.btc > 0)
        history.append(bar)
        portfolio.mark(bar.close, bar.close_time, "close")
        state.observe(
            history,
            portfolio.cash,
            portfolio.btc,
            portfolio.peak,
            costs.sell_fee,
            costs.sell_slippage,
            delay=delay,
            halted=portfolio.halted,
        )
        if portfolio.halted:
            if portfolio.btc <= 0:
                pending = None
            elif pending is None or pending[2] != "risk":
                pending = (i + 1 + delay, "sell", "risk", bar.close_time)
        elif pending is None:
            next_signal = state.signal(history, portfolio.btc > 0)
            if next_signal:
                pending = (i + 1 + delay, next_signal, "signal", bar.close_time)
    last = bars[-1]
    if portfolio.btc > 0:
        portfolio.trade("sell", last.close, last.close_time, "settlement", None)
    portfolio.mark(last.close, last.close_time, "settlement")
    return Result(
        strategy="regime-mlp-v1",
        costs=costs,
        initial_capital=capital,
        start=start,
        end=last.close_time,
        extra_delay_bars=delay,
        cash=portfolio.cash,
        btc=portfolio.btc,
        final_equity=portfolio.cash + portfolio.btc * last.close,
        net_return=(portfolio.cash + portfolio.btc * last.close) / capital - 1,
        max_drawdown=portfolio.max_drawdown,
        total_fees=sum((f.fee for f in portfolio.fills), Decimal(0)),
        total_slippage=sum((f.slippage_cost for f in portfolio.fills), Decimal(0)),
        natural_exits=sum(f.side == "sell" and f.reason != "settlement" for f in portfolio.fills),
        exposure_ratio=Decimal(exposure) / (len(bars) - first),
        halted=portfolio.halted,
        fills=portfolio.fills,
        rejections=portfolio.rejections,
        equity_curve=portfolio.curve,
    )
