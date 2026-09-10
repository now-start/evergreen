"""Frozen early3 policy: shared by event replay and real-order execution, no I/O."""

from collections.abc import Sequence
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from evergreen.market import Candle, Nonnegative, Positive
from evergreen.strategies.breakout import target
from evergreen.strategies.types import Side

ID: Final = "breakout-buffer-early3-v1"
ExecutionStrategy = Literal["breakout-v1", "breakout-buffer-early3-v1"]
LIMIT = Decimal(".20")
ONE = Decimal(1)
ZERO = Decimal(0)
Finite = Annotated[Decimal, Field(allow_inf_nan=False)]


def prior_mean_true_range(history: Sequence[Candle], *, lookback: int = 24) -> Decimal:
    """Exclude the last signal bar; retain the prior close for each true range."""
    if lookback not in (24, 168) or len(history) < lookback + 2:
        raise ValueError("이전 TR 계산을 위한 연속 봉이 부족하거나 기간이 잘못됐습니다")
    return (
        sum(
            (
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous.close),
                    abs(bar.low - previous.close),
                )
                for previous, bar in zip(
                    history[-lookback - 2 : -2], history[-lookback - 1 : -1], strict=True
                )
            ),
            ZERO,
        )
        / lookback
    )


def reset_required(history: Sequence[Candle]) -> bool:
    return history[-1].close >= min(b.low for b in history[-49:-1])


def entry_budget(
    cash: Decimal,
    peak: Decimal,
    reference: Decimal,
    tr: Decimal,
    buy_fee: Decimal,
    sell_fee: Decimal,
    buy_slippage: Decimal,
    sell_slippage: Decimal,
    step: Decimal,
) -> bool:
    floor = reference - 6 * tr
    if tr <= 0 or floor <= 0:
        return False
    price = reference * (ONE + buy_slippage)
    quantity = (cash / (ONE + buy_fee) / price / step).to_integral_value(rounding=ROUND_DOWN) * step
    bought = quantity * price
    sold = quantity * (floor * (ONE - sell_slippage))
    projected = cash - (bought + bought * buy_fee)
    projected += sold - sold * sell_fee
    return projected >= peak * (ONE - LIMIT)


class Protection(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    reference: Positive
    entry_tr: Positive
    peak: Positive
    threshold: Finite
    breaches: int = Field(default=0, ge=0)

    @classmethod
    def open(cls, reference: Decimal, tr: Decimal) -> "Protection":
        return cls(reference=reference, entry_tr=tr, peak=reference, threshold=reference - 6 * tr)

    def observe(self, history: Sequence[Candle]) -> None:
        close = history[-1].close
        self.peak = max(self.peak, close)
        activated = self.peak >= self.reference + 3 * self.entry_tr
        threshold = self.reference - 6 * self.entry_tr
        if activated:
            threshold = self.peak - 6 * max(self.entry_tr, prior_mean_true_range(history))
        self.threshold = max(self.threshold, threshold)
        rising = sum(b.close for b in history[-25:-1]) > sum(b.close for b in history[-49:-25])
        breach = close < self.reference - 6 * self.entry_tr or (
            activated and close < self.threshold and not rising
        )
        self.breaches = self.breaches + 1 if breach else 0


class BufferState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    protection: Protection | None = None
    awaiting_reset: bool = False
    exit_due: bool = False
    last_bar: AwareDatetime | None = None
    position_since: AwareDatetime | None = None

    def observe(
        self,
        history: Sequence[Candle],
        cash: Decimal,
        btc: Decimal,
        peak: Decimal,
        sell_fee: Decimal,
        sell_slippage: Decimal,
        *,
        delay: int = 0,
        halted: bool = False,
    ) -> None:
        time = history[-1].close_time
        if self.last_bar == time:
            return
        if self.last_bar is not None and time != self.last_bar + timedelta(hours=1):
            raise ValueError("후보의 봉 처리 순서가 연속적이지 않습니다")
        if btc > 0:
            if self.protection is None:
                raise ValueError("보유 포지션의 보호 상태가 없습니다")
            self.protection.observe(history)
            if not halted:
                price = max(ZERO, history[-1].close - (1 + delay) * prior_mean_true_range(history))
                liquidation = cash + btc * price * (ONE - sell_slippage) * (ONE - sell_fee)
                self.exit_due = self.exit_due or liquidation <= peak * (ONE - LIMIT)
        self.last_bar = time

    def signal(self, history: Sequence[Candle], holding: bool) -> Side | None:
        if not holding:
            if self.awaiting_reset:
                if not reset_required(history):
                    self.awaiting_reset = False
                return None
            return target(history, False)
        if self.protection is None:
            raise ValueError("보유 포지션의 보호 상태가 없습니다")
        if self.exit_due or self.protection.breaches >= 2:
            return "sell"
        return "sell" if history[-1].close < min(b.low for b in history[-97:-1]) else None

    def bought(self, reference: Decimal, tr: Decimal) -> None:
        self.protection = Protection.open(reference, tr)
        self.awaiting_reset = self.exit_due = False
        self.position_since = None

    def sold(self, *, reset: bool) -> None:
        self.protection = None
        self.exit_due = False
        self.awaiting_reset = reset
        self.position_since = None


class IntentContext(BaseModel):
    """Persisted alongside, but never sent as part of an exchange order payload."""

    model_config = ConfigDict(extra="forbid")
    signal_time: AwareDatetime
    entry_tr: Nonnegative = ZERO
    reset_after_sell: bool = False
    reason: Literal["signal", "risk"]
