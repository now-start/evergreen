"""Deterministic, all-in spot simulation. This module cannot submit real orders."""

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from evergreen.market import Candle, Nonnegative, Positive, utc_hour, validate_candles
from evergreen.strategies import (
    PREDICTIVE_STRATEGIES,
    STRATEGY_PARAMETERS,
    Side,
    Strategy,
    signal_target,
    warmup_bars,
)

Reason = Literal["signal", "benchmark", "risk", "settlement"]
Rate = Annotated[Decimal, Field(ge=0, lt=1, allow_inf_nan=False)]
ZERO = Decimal(0)
ONE = Decimal(1)
DRAWDOWN_LIMIT = Decimal("0.10")


class Costs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    buy_fee: Rate
    sell_fee: Rate
    buy_slippage: Rate
    sell_slippage: Rate
    min_notional: Positive
    quantity_step: Positive


class Fill(BaseModel):
    time: datetime
    signal_time: datetime | None
    side: Side
    reason: Reason
    reference_price: Decimal
    price: Decimal
    quantity: Decimal
    fee: Decimal
    slippage_cost: Decimal
    cash_after: Decimal
    btc_after: Decimal


class Rejection(BaseModel):
    time: datetime
    side: Side
    reason: Literal["below_minimum"] = "below_minimum"
    trigger: Reason


class EquityPoint(BaseModel):
    time: datetime
    phase: Literal["open", "close", "settlement"]
    equity: Decimal
    drawdown: Decimal


class Result(BaseModel):
    strategy: Strategy
    costs: Costs
    initial_capital: Positive
    start: datetime
    end: datetime
    extra_delay_bars: int
    cash: Nonnegative
    btc: Nonnegative
    final_equity: Nonnegative
    net_return: Decimal
    max_drawdown: Nonnegative
    total_fees: Nonnegative
    total_slippage: Nonnegative
    natural_exits: int
    exposure_ratio: Nonnegative
    halted: bool
    fills: list[Fill]
    rejections: list[Rejection]
    equity_curve: list[EquityPoint]


class _Portfolio:
    def __init__(self, capital: Decimal, costs: Costs, guard: bool) -> None:
        self.cash, self.btc = capital, ZERO
        self.costs, self.guard = costs, guard
        self.peak, self.max_drawdown = capital, ZERO
        self.halted = False
        self.fills: list[Fill] = []
        self.rejections: list[Rejection] = []
        self.curve: list[EquityPoint] = []

    def mark(
        self, price: Decimal, time: datetime, phase: Literal["open", "close", "settlement"]
    ) -> None:
        equity = self.cash + self.btc * price
        self.peak = max(self.peak, equity)
        drawdown = ONE - equity / self.peak
        self.max_drawdown = max(self.max_drawdown, drawdown)
        if self.guard and drawdown >= DRAWDOWN_LIMIT:
            self.halted = True
        self.curve.append(EquityPoint(time=time, phase=phase, equity=equity, drawdown=drawdown))

    def trade(
        self,
        side: Side,
        reference: Decimal,
        time: datetime,
        reason: Reason,
        signal_time: datetime | None,
    ) -> None:
        if side == "buy":
            price = reference * (ONE + self.costs.buy_slippage)
            available = self.cash / (ONE + self.costs.buy_fee) / price
            fee_rate = self.costs.buy_fee
        else:
            price = reference * (ONE - self.costs.sell_slippage)
            available = self.btc
            fee_rate = self.costs.sell_fee
        quantity = (available / self.costs.quantity_step).to_integral_value(
            rounding=ROUND_DOWN
        ) * self.costs.quantity_step
        notional = quantity * price
        fee = notional * fee_rate
        if quantity <= 0 or notional < self.costs.min_notional:
            self.rejections.append(Rejection(time=time, side=side, trigger=reason))
            return
        if side == "buy":
            self.cash -= notional + fee
            self.btc += quantity
        else:
            self.cash += notional - fee
            self.btc -= quantity
        self.fills.append(
            Fill(
                time=time,
                signal_time=signal_time,
                side=side,
                reason=reason,
                reference_price=reference,
                price=price,
                quantity=quantity,
                fee=fee,
                slippage_cost=quantity * abs(price - reference),
                cash_after=self.cash,
                btc_after=self.btc,
            )
        )


def run_backtest(
    candles: Sequence[Candle],
    start: datetime,
    capital: Decimal,
    costs: Costs,
    strategy: Strategy,
    *,
    extra_delay_bars: int = 0,
    predictions: dict[datetime, Decimal] | None = None,
) -> Result:
    start = utc_hour(start)
    if not capital.is_finite() or capital <= 0:
        raise ValueError("capital must be finite and positive")
    if strategy not in STRATEGY_PARAMETERS:
        raise ValueError("unknown strategy")
    if extra_delay_bars not in (0, 1):
        raise ValueError("extra_delay_bars must be 0 or 1")
    if not candles:
        raise ValueError("empty dataset")
    bars, quality = validate_candles(
        candles, min(c.open_time for c in candles), max(c.close_time for c in candles)
    )
    if not quality.valid or quality.duplicates or quality.incomplete:
        raise ValueError("candle quality failed")
    indices = {bar.open_time: index for index, bar in enumerate(bars)}
    if start not in indices or indices[start] < warmup_bars(strategy):
        raise ValueError(
            f"evaluation requires {warmup_bars(strategy)} hours of warmup and a matching start"
        )
    first = indices[start]
    if strategy in PREDICTIVE_STRATEGIES:
        expected = {bar.close_time for bar in bars[first - 1 :]}
        if (
            predictions is None
            or set(predictions) != expected
            or any(
                not value.is_finite() or not ZERO <= value <= ONE for value in predictions.values()
            )
        ):
            raise ValueError("예측 확률의 시각·범위·완전성이 잘못됐습니다")
    elif predictions is not None:
        raise ValueError("규칙 기반 전략에는 예측 확률을 전달할 수 없습니다")

    entry_time: datetime | None = None

    def target(history: Sequence[Candle], holding: bool) -> Side | None:
        return signal_target(
            history,
            holding,
            strategy,
            probability=predictions[history[-1].close_time] if predictions is not None else None,
            entry_time=entry_time,
        )

    active = strategy not in ("cash", "buy-hold")
    portfolio = _Portfolio(capital, costs, active)
    history = bars[:first]
    # (execution bar index, side, reason, signal timestamp). One pending order at a time.
    pending: tuple[int, Side, Reason, datetime | None] | None = None
    if strategy == "buy-hold":
        pending = (first + extra_delay_bars, "buy", "benchmark", None)
    elif active:
        initial = target(history, False)
        if initial:
            pending = (first + extra_delay_bars, initial, "signal", bars[first - 1].close_time)
    exposure = 0
    for index in range(first, len(bars)):
        bar = bars[index]
        if pending and pending[0] == index:
            _, side, reason, signal_time = pending
            previous_fills = len(portfolio.fills)
            portfolio.trade(side, bar.open, bar.open_time, reason, signal_time)
            if len(portfolio.fills) > previous_fills:
                entry_time = bar.open_time if side == "buy" else None
            pending = None
        portfolio.mark(bar.open, bar.open_time, "open")
        exposure += int(portfolio.btc > 0)
        history.append(bar)
        portfolio.mark(bar.close, bar.close_time, "close")
        if portfolio.halted:
            # Risk replaces strategy orders, but an existing risk order keeps its due bar.
            if portfolio.btc <= 0:
                pending = None
            elif pending is None or pending[2] != "risk":
                pending = (index + 1 + extra_delay_bars, "sell", "risk", bar.close_time)
        elif active and pending is None:
            signal = target(history, portfolio.btc > 0)
            if signal:
                pending = (index + 1 + extra_delay_bars, signal, "signal", bar.close_time)
    last = bars[-1]
    if portfolio.btc > 0:
        portfolio.trade("sell", last.close, last.close_time, "settlement", None)
    portfolio.mark(last.close, last.close_time, "settlement")
    final = portfolio.cash + portfolio.btc * last.close
    return Result(
        strategy=strategy,
        costs=costs,
        initial_capital=capital,
        start=start,
        end=last.close_time,
        extra_delay_bars=extra_delay_bars,
        cash=portfolio.cash,
        btc=portfolio.btc,
        final_equity=final,
        net_return=final / capital - ONE,
        max_drawdown=portfolio.max_drawdown,
        total_fees=sum((fill.fee for fill in portfolio.fills), ZERO),
        total_slippage=sum((fill.slippage_cost for fill in portfolio.fills), ZERO),
        natural_exits=sum(
            fill.side == "sell" and fill.reason != "settlement" for fill in portfolio.fills
        ),
        exposure_ratio=Decimal(exposure) / (len(bars) - first),
        halted=portfolio.halted,
        fills=portfolio.fills,
        rejections=portfolio.rejections,
        equity_curve=portfolio.curve,
    )
