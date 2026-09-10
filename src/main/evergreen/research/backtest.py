"""Deterministic, all-in spot simulation. This module cannot submit real orders."""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from evergreen.market import Candle, Nonnegative, Positive, utc_hour, validate_candles
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.strategies import (
    PREDICTIVE_STRATEGIES,
    STRATEGY_PARAMETERS,
    Side,
    Strategy,
    signal_target,
    warmup_bars,
)
from evergreen.strategies.regime import REGIME_STRATEGIES, RegimePolicy, RegimeRouter

Reason = Literal["signal", "benchmark", "risk", "settlement"]
Rate = Annotated[Decimal, Field(ge=0, lt=1, allow_inf_nan=False)]
ZERO = Decimal(0)
ONE = Decimal(1)
DRAWDOWN_LIMIT = Decimal("0.10")


class ExitPolicy(BaseModel):
    """Bounded offline exit sensitivity; production settings are not read or changed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    drawdown_limit: Annotated[Decimal, Field(gt=0, le=Decimal(".2"), allow_inf_nan=False)] = (
        DRAWDOWN_LIMIT
    )
    stop_multiple: Annotated[Decimal, Field(ge=3, le=6, allow_inf_nan=False)] = Decimal(3)
    channel_hours: Literal[48, 72, 96] = 48
    profit_activation_multiple: (
        Annotated[Decimal, Field(ge=3, le=6, allow_inf_nan=False)] | None
    ) = None


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
    reason: Literal["below_minimum", "risk_budget"] = "below_minimum"
    trigger: Reason


class EquityPoint(BaseModel):
    time: datetime
    phase: Literal["open", "close", "settlement"]
    equity: Decimal
    drawdown: Decimal


class ExitDecision(BaseModel):
    """Offline experiment 52: immutable state before the first extra exit reservation."""

    model_config = ConfigDict(frozen=True)

    signal_time: datetime
    entry_fill: Fill
    cash: Decimal
    btc: Decimal
    account_peak: Decimal
    max_drawdown: Decimal
    equity: Decimal
    entry_true_range: Decimal
    entry_reference: Decimal
    trailing_peak: Decimal
    threshold: Decimal
    short_breaches: int
    long_breaches: int
    prefix_sha256: str
    candles_sha256: str


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
    def __init__(
        self,
        capital: Decimal,
        costs: Costs,
        guard: bool,
        *,
        drawdown_limit: Decimal = DRAWDOWN_LIMIT,
    ) -> None:
        self.cash, self.btc = capital, ZERO
        self.costs, self.guard = costs, guard
        self.drawdown_limit = drawdown_limit
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
        if self.guard and drawdown >= self.drawdown_limit:
            self.halted = True
        self.curve.append(EquityPoint(time=time, phase=phase, equity=equity, drawdown=drawdown))

    def channel_entry_fits(self, reference: Decimal, floor: Decimal) -> bool:
        price = reference * (ONE + self.costs.buy_slippage)
        quantity = (
            self.cash / (ONE + self.costs.buy_fee) / price / self.costs.quantity_step
        ).to_integral_value(rounding=ROUND_DOWN) * self.costs.quantity_step
        buy_notional = quantity * price
        sell_notional = quantity * (floor * (ONE - self.costs.sell_slippage))
        projected = self.cash - (buy_notional + buy_notional * self.costs.buy_fee)
        projected += sell_notional - sell_notional * self.costs.sell_fee
        return projected >= self.peak * (ONE - self.drawdown_limit)

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


def _entry_trend_context(history: Sequence[Candle]) -> int:
    """Choose once from the original signal close; its TR excludes that candle."""
    if len(history) < 26:
        raise ValueError("진입 맥락에 필요한 과거 봉이 없습니다")
    advance = history[-1].close - history[-25].close
    return 168 if advance > 3 * prior_mean_true_range(history) else 24


def _entry_path_context(history: Sequence[Candle]) -> int:
    """Choose from 24 completed close-to-close moves ending at the signal."""
    if len(history) < 25:
        raise ValueError("진입 경로에 필요한 과거 봉이 없습니다")
    advance = history[-1].close - history[-25].close
    travel = sum(
        abs(current.close - previous.close)
        for previous, current in zip(history[-25:-1], history[-24:], strict=True)
    )
    return 168 if 2 * advance > travel else 24


def run_backtest(
    candles: Sequence[Candle],
    start: datetime,
    capital: Decimal,
    costs: Costs,
    strategy: Strategy,
    *,
    extra_delay_bars: int = 0,
    predictions: dict[datetime, Decimal] | None = None,
    regimes: dict[datetime, int] | None = None,
    regime_policy: RegimePolicy = "routing",
    breakout_exit_lookback: int = 48,
    breakout_cooldown_hours: int = 0,
    breakout_risk_budget: bool = False,
    breakout_failure_exit: bool = False,
    breakout_failure_confirmations: int = 1,
    breakout_failure_buffer: bool = False,
    breakout_trailing_exit: bool = False,
    breakout_trailing_profit_only: bool = False,
    breakout_trailing_adaptive: bool = False,
    breakout_rejection_latch: bool = False,
    breakout_entry_stop: bool = False,
    breakout_entry_stop_floor: bool = False,
    breakout_entry_stop_expansion: bool = False,
    breakout_entry_stop_confirmations: int = 1,
    breakout_entry_stop_channel_reset: bool = False,
    breakout_entry_stop_profit_trail: bool = False,
    breakout_entry_stop_adaptive_trail: bool = False,
    breakout_entry_stop_loss_reset: bool = False,
    breakout_confirmed_profit_reentry: bool = False,
    breakout_entry_stop_trend_confirmation: bool = False,
    breakout_entry_stop_budget: bool = False,
    breakout_entry_stop_trend_lookback: int = 24,
    breakout_exit_checkpoint: bool = False,
    breakout_exit_higher_low: bool = False,
    breakout_extension_floor: bool = False,
    breakout_liquidation_buffer: bool = False,
    breakout_entry_lookback: int = 168,
    breakout_entry_trend_filter: bool = False,
    breakout_entry_slope_filter: bool = False,
    breakout_entry_cadence_hours: int = 1,
    breakout_entry_context: bool = False,
    breakout_entry_path_context: bool = False,
    long_entry_at: datetime | None = None,
    exit_decisions: list[ExitDecision] | None = None,
    extend_exit_at: datetime | None = None,
    exit_policy: ExitPolicy | None = None,
) -> Result:
    start = utc_hour(start)
    policy = exit_policy or ExitPolicy()
    stop_multiple = policy.stop_multiple
    activation_multiple = policy.profit_activation_multiple or stop_multiple
    if policy.profit_activation_multiple is not None and not breakout_entry_stop_adaptive_trail:
        raise ValueError("수익 추적 활성화 분리는 적응 추적 손절에만 적용합니다")
    if policy != ExitPolicy() and (
        not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
        or breakout_exit_lookback != 48
        or breakout_trailing_exit
        or breakout_failure_exit
        or breakout_entry_stop_floor
        or breakout_entry_stop_expansion
        or breakout_exit_checkpoint
        or breakout_exit_higher_low
        or breakout_extension_floor
        or exit_decisions is not None
        or extend_exit_at is not None
        or long_entry_at is not None
    ):
        raise ValueError("완화 정책은 추가 분기 없는 돌파 청산 비교에만 사용합니다")
    if not capital.is_finite() or capital <= 0:
        raise ValueError("capital must be finite and positive")
    if strategy not in STRATEGY_PARAMETERS:
        raise ValueError("unknown strategy")
    if regime_policy != "routing" and strategy not in REGIME_STRATEGIES:
        raise ValueError("장세 전략 외에는 장세 정책을 전달할 수 없습니다")
    if extra_delay_bars not in (0, 1):
        raise ValueError("extra_delay_bars must be 0 or 1")
    if breakout_exit_lookback not in (24, 48) or (
        breakout_exit_lookback != 48
        and not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
    ):
        raise ValueError("연구용 청산 채널은 돌파 전략의 24/48시간만 지원합니다")
    if breakout_cooldown_hours not in (0, 24) or (
        breakout_cooldown_hours
        and not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
    ):
        raise ValueError("연구용 재진입 대기는 돌파 전략의 0/24시간만 지원합니다")
    if not candles:
        raise ValueError("empty dataset")
    observe_exits = exit_decisions is not None or extend_exit_at is not None
    if breakout_entry_cadence_hours not in (1, 4) or (
        breakout_entry_cadence_hours != 1
        and (
            not breakout_liquidation_buffer
            or breakout_entry_lookback != 168
            or breakout_entry_stop_trend_lookback != 24
            or breakout_entry_context
            or breakout_entry_path_context
            or long_entry_at is not None
        )
    ):
        raise ValueError("진입 간격은 고정 정책57의 1/4시간만 지원합니다")
    if breakout_entry_slope_filter and (
        breakout_entry_lookback != 84 or breakout_entry_trend_filter
    ):
        raise ValueError("장기 평균 방향은 가격 위치 조건 없는 84시간 진입 창에만 적용합니다")
    if breakout_entry_trend_filter and breakout_entry_lookback != 84:
        raise ValueError("장기 평균가격 조건은 84시간 진입 창에만 적용합니다")
    if breakout_entry_lookback not in (84, 168) or (
        breakout_entry_lookback != 168
        and (
            not breakout_liquidation_buffer
            or breakout_entry_stop_trend_lookback != 24
            or breakout_entry_context
            or breakout_entry_path_context
            or long_entry_at is not None
        )
    ):
        raise ValueError("연구용 진입 창은 고정 정책57의 84/168시간만 지원합니다")
    if long_entry_at is not None:
        long_entry_at = utc_hour(long_entry_at)
        if (
            not breakout_liquidation_buffer
            or breakout_entry_stop_trend_lookback != 24
            or breakout_entry_context
            or breakout_entry_path_context
        ):
            raise ValueError("진입 분기는 고정된 짧은 추세 청산 여유에만 적용합니다")
    if breakout_entry_path_context and (
        not breakout_liquidation_buffer
        or breakout_entry_stop_trend_lookback != 24
        or breakout_entry_context
    ):
        raise ValueError("진입 경로는 다른 선택 조건 없는 짧은 추세 청산 여유 후보에만 적용합니다")
    if breakout_entry_context and (
        not breakout_liquidation_buffer or breakout_entry_stop_trend_lookback != 24
    ):
        raise ValueError("진입 맥락은 짧은 추세 청산 여유 후보에만 적용합니다")
    if breakout_liquidation_buffer and (
        not breakout_entry_stop_budget
        or breakout_entry_stop_trend_lookback not in (24, 168)
        or observe_exits
        or breakout_exit_checkpoint
        or breakout_exit_higher_low
        or breakout_extension_floor
    ):
        raise ValueError("청산 여유는 다른 연장 조건 없는 정책50/51에만 적용합니다")
    if breakout_extension_floor and (
        not breakout_entry_stop_budget
        or breakout_entry_stop_trend_lookback != 24
        or observe_exits
        or breakout_exit_checkpoint
        or breakout_exit_higher_low
    ):
        raise ValueError("연장 가격 확인은 다른 분기 조건 없는 정책50에만 적용합니다")
    if breakout_exit_higher_low and (
        not breakout_entry_stop_budget
        or breakout_entry_stop_trend_lookback != 24
        or observe_exits
        or breakout_exit_checkpoint
    ):
        raise ValueError("저점 상승 연장은 수동 분기·중간 점검 없는 정책50에만 적용합니다")
    if breakout_exit_checkpoint and (
        not breakout_entry_stop_budget or breakout_entry_stop_trend_lookback != 24 or observe_exits
    ):
        raise ValueError("중간 점검은 수동 분기 없는 정책50에만 적용합니다")
    if observe_exits and (
        not breakout_entry_stop_budget or breakout_entry_stop_trend_lookback != 24
    ):
        raise ValueError("청산 분기는 정책50의 짧은 추세 손절 예산에만 적용합니다")
    if extend_exit_at is not None:
        extend_exit_at = utc_hour(extend_exit_at)
    if breakout_entry_stop_trend_lookback not in (24, 168) or (
        breakout_entry_stop_trend_lookback != 24 and not breakout_entry_stop_budget
    ):
        raise ValueError("추세 맥락은 손절 예산 후보의 이전 24/168시간만 지원합니다")
    if breakout_entry_stop_budget and not breakout_entry_stop_trend_confirmation:
        raise ValueError("손절 예산은 추세 확인 후보에만 적용합니다")
    if breakout_entry_stop_trend_confirmation and (
        not breakout_entry_stop_adaptive_trail or breakout_entry_stop_loss_reset
    ):
        raise ValueError("추세 확인은 재진입 변형 없는 적응 추적 후보에만 적용합니다")
    if breakout_confirmed_profit_reentry and not breakout_entry_stop_loss_reset:
        raise ValueError("수익 재진입 확인은 손실 리셋 후보에만 적용합니다")
    if breakout_entry_stop_loss_reset and not breakout_entry_stop_adaptive_trail:
        raise ValueError("손실 리셋은 적응 추적과 채널 리셋에만 적용합니다")
    if breakout_entry_stop_adaptive_trail and not breakout_entry_stop_profit_trail:
        raise ValueError("적응 추적은 상승 후 추적과 채널 리셋에만 적용합니다")
    if breakout_entry_stop_profit_trail and not breakout_entry_stop_channel_reset:
        raise ValueError("상승 후 추적은 두 종가 손절과 채널 리셋에만 적용합니다")
    if breakout_entry_stop_channel_reset and (
        not breakout_entry_stop or breakout_entry_stop_confirmations != 2
    ):
        raise ValueError("채널 리셋은 두 종가 고정 손절에만 적용합니다")
    if breakout_entry_stop_confirmations not in (1, 2) or (
        breakout_entry_stop_confirmations != 1
        and (not breakout_entry_stop or breakout_entry_stop_floor or breakout_entry_stop_expansion)
    ):
        raise ValueError("고정 손절 확인은 하한/확대 없는 해당 청산의 1/2시간만 지원합니다")
    if breakout_entry_stop_floor and not breakout_entry_stop:
        raise ValueError("장기 변동 폭 하한은 고정 손절에만 적용합니다")
    if breakout_entry_stop_expansion and (not breakout_entry_stop or breakout_entry_stop_floor):
        raise ValueError("확대 조건은 하한 없는 고정 손절에만 적용합니다")
    if breakout_entry_stop and (
        breakout_exit_lookback != 48
        or breakout_cooldown_hours != 0
        or breakout_risk_budget
        or breakout_failure_exit
        or breakout_trailing_exit
        or breakout_rejection_latch
        or not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
    ):
        raise ValueError("고정 손절은 원래 돌파 전략에 단독 적용합니다")
    if breakout_rejection_latch and (
        strategy not in REGIME_STRATEGIES
        or regime_policy != "breakout-filter"
        or breakout_exit_lookback != 48
        or breakout_cooldown_hours != 0
        or breakout_risk_budget
        or breakout_failure_exit
        or breakout_trailing_exit
    ):
        raise ValueError("거절 기록은 원래 돌파 진입 필터에 단독 적용합니다")
    if breakout_trailing_profit_only and not breakout_trailing_exit:
        raise ValueError("상승 후 활성 조건은 고점 추적 청산에만 적용합니다")
    if breakout_trailing_adaptive and not breakout_trailing_profit_only:
        raise ValueError("가변 추적 거리는 상승 후 활성 청산에만 적용합니다")
    if breakout_trailing_exit and (
        breakout_failure_exit
        or breakout_exit_lookback != 48
        or breakout_cooldown_hours != 0
        or breakout_risk_budget
        or not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
    ):
        raise ValueError("연구용 고점 추적 청산은 원래 돌파 전략에 단독 적용합니다")
    if breakout_failure_buffer and (
        not breakout_failure_exit or breakout_failure_confirmations != 1
    ):
        raise ValueError("돌파 실패 변동 폭은 1시간 실패 청산에 단독 적용합니다")
    if breakout_failure_confirmations not in (1, 2) or (
        breakout_failure_confirmations != 1 and not breakout_failure_exit
    ):
        raise ValueError("돌파 실패 확인은 해당 청산의 1/2시간만 지원합니다")
    if breakout_failure_exit and (
        breakout_exit_lookback != 48
        or breakout_cooldown_hours != 0
        or breakout_risk_budget
        or not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
    ):
        raise ValueError("연구용 돌파 실패 청산은 원래 돌파 전략에 단독 적용합니다")
    if breakout_risk_budget and (
        breakout_exit_lookback != 48
        or breakout_cooldown_hours != 0
        or not (
            strategy == "breakout-v1"
            or (strategy in REGIME_STRATEGIES and regime_policy == "breakout-filter")
        )
    ):
        raise ValueError("연구용 위험 여유 검사는 원래 48시간 돌파 청산에만 적용합니다")
    bars, quality = validate_candles(
        candles, min(c.open_time for c in candles), max(c.close_time for c in candles)
    )
    if not quality.valid or quality.duplicates or quality.incomplete:
        raise ValueError("candle quality failed")
    indices = {bar.open_time: index for index, bar in enumerate(bars)}
    required_warmup = max(
        warmup_bars(strategy),
        193 if breakout_entry_slope_filter else 0,
        170 if breakout_entry_stop_floor or breakout_entry_stop_expansion else 0,
        192
        if breakout_entry_stop_trend_lookback == 168
        or breakout_entry_context
        or breakout_entry_path_context
        or long_entry_at is not None
        or observe_exits
        or breakout_exit_checkpoint
        or breakout_exit_higher_low
        else 0,
        192 if breakout_extension_floor else 0,
    )
    if start not in indices or indices[start] < required_warmup:
        raise ValueError(
            f"evaluation requires {required_warmup} hours of warmup and a matching start"
        )
    first = indices[start]
    if strategy in REGIME_STRATEGIES:
        expected = {bar.close_time for bar in bars[first - 1 :]}
        if (
            regimes is None
            or set(regimes) != expected
            or any(v not in (-1, 0, 1, 2) for v in regimes.values())
        ):
            raise ValueError("장세 판단의 시각·범위·완전성이 잘못됐습니다")
    elif regimes is not None:
        raise ValueError("장세 전략 외에는 장세 판단을 전달할 수 없습니다")
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
    last_exit: datetime | None = None
    entry_breakout_level: Decimal | None = None
    entry_true_range: Decimal | None = None
    position_trend_lookback = breakout_entry_stop_trend_lookback
    entry_fork_matched = False
    trailing_peak: Decimal | None = None
    entry_reference: Decimal | None = None
    entry_stop_active = False
    awaiting_channel_reset = False
    profit_reentry_after: datetime | None = None
    entry_stop_breaches = 0
    long_breaches = 0
    decision_recorded = False
    extend_position = False
    fork_matched = False
    checkpoint_at: datetime | None = None
    checkpoint_reference: Decimal | None = None
    checkpoint_exit_due = False
    extension_floor_breaches = 0
    liquidation_exit_due = False
    entry_stop_threshold: Decimal | None = None
    rejected_ceiling: Decimal | None = None
    router = RegimeRouter(regime_policy)

    def target(history: Sequence[Candle], holding: bool) -> Side | None:
        nonlocal rejected_ceiling, awaiting_channel_reset, profit_reentry_after
        if holding and (checkpoint_exit_due or liquidation_exit_due):
            return "sell"
        if not holding and awaiting_channel_reset:
            if history[-1].close < min(b.low for b in history[-49:-1]):
                awaiting_channel_reset = False
            return None
        if not holding and profit_reentry_after is not None:
            if history[-1].close < min(b.low for b in history[-49:-1]):
                profit_reentry_after = None
                return None
            if (
                history[-2].open_time < profit_reentry_after
                or signal_target(history[:-1], False, "breakout-v1") != "buy"
            ):
                return None
        if breakout_rejection_latch and not holding and rejected_ceiling is not None:
            if history[-1].close > rejected_ceiling:
                return None
            rejected_ceiling = None
        if (
            not holding
            and last_exit is not None
            and history[-1].close_time < last_exit + timedelta(hours=breakout_cooldown_hours)
        ):
            return None
        if holding and breakout_failure_exit:
            if entry_breakout_level is None or entry_time is None:
                raise ValueError("돌파 실패 청산의 진입 기준이 없습니다")
            if all(
                b.open_time >= entry_time and b.close < entry_breakout_level
                for b in history[-breakout_failure_confirmations:]
            ):
                return "sell"
        if holding and breakout_trailing_exit:
            if entry_true_range is None or trailing_peak is None or entry_reference is None:
                raise ValueError("고점 추적 청산의 진입 기준이 없습니다")
            distance = 3 * entry_true_range
            if entry_true_range > 0 and (
                not breakout_trailing_profit_only or trailing_peak >= entry_reference + distance
            ):
                if breakout_trailing_adaptive:
                    distance = 3 * max(entry_true_range, prior_mean_true_range(history))
                if history[-1].close < trailing_peak - distance:
                    return "sell"
        if holding and breakout_entry_stop and entry_stop_active:
            if entry_true_range is None or entry_reference is None or entry_time is None:
                raise ValueError("고정 손절의 실제 매수 기준이 없습니다")
            if breakout_entry_stop_profit_trail:
                confirmed = (
                    long_breaches if extend_position else entry_stop_breaches
                ) >= breakout_entry_stop_confirmations
            else:
                confirmed = all(
                    b.open_time >= entry_time
                    and b.close < entry_reference - stop_multiple * entry_true_range
                    for b in history[-breakout_entry_stop_confirmations:]
                )
            if entry_true_range > 0 and confirmed:
                return "sell"
        if holding and (breakout_exit_lookback == 24 or policy.channel_hours != 48):
            channel = 24 if breakout_exit_lookback == 24 else policy.channel_hours
            floor = min(bar.low for bar in history[-channel - 1 : -1])
            return "sell" if history[-1].close < floor else None
        if not holding and breakout_entry_lookback == 84:
            if regimes is not None and regimes[history[-1].close_time] != 2:
                return None
            if breakout_entry_slope_filter and sum(
                (bar.close for bar in history[-169:-1]), ZERO
            ) <= sum((bar.close for bar in history[-193:-25]), ZERO):
                return None
            if breakout_entry_trend_filter and history[-1].close * 168 <= sum(
                (bar.close for bar in history[-169:-1]), ZERO
            ):
                return None
            ceiling = max(bar.high for bar in history[-85:-1])
            return "buy" if history[-1].close > ceiling * Decimal("1.001") else None
        if regimes is not None:
            signal = router.target(history, holding, regimes[history[-1].close_time])
            if (
                breakout_rejection_latch
                and not holding
                and signal is None
                and signal_target(history, False, "breakout-v1") == "buy"
            ):
                rejected_ceiling = max(b.high for b in history[-169:-1])
        else:
            signal = signal_target(
                history,
                holding,
                strategy,
                probability=predictions[history[-1].close_time]
                if predictions is not None
                else None,
                entry_time=entry_time,
            )
        if (
            signal == "buy"
            and breakout_risk_budget
            and not portfolio.channel_entry_fits(
                history[-1].close, min(bar.low for bar in history[-49:-1])
            )
        ):
            return None
        # Keep hourly state maintenance, exits and pending fills outside this entry-only gate.
        if signal == "buy" and history[-1].close_time.hour % breakout_entry_cadence_hours:
            return None
        return signal

    active = strategy not in ("cash", "buy-hold")
    portfolio = _Portfolio(capital, costs, active, drawdown_limit=policy.drawdown_limit)
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
            budget_fits = True
            if side == "buy" and breakout_entry_stop_budget:
                if signal_time is None:
                    raise ValueError("손절 예산의 원래 진입 신호 시각이 없습니다")
                signal_index = indices[signal_time] - 1
                signal_tr = prior_mean_true_range(bars[signal_index - 25 : signal_index + 1])
                initial_stop = bar.open - stop_multiple * signal_tr
                budget_fits = (
                    signal_tr > ZERO
                    and initial_stop > ZERO
                    and portfolio.channel_entry_fits(bar.open, initial_stop)
                )
            if budget_fits:
                portfolio.trade(side, bar.open, bar.open_time, reason, signal_time)
            else:
                portfolio.rejections.append(
                    Rejection(time=bar.open_time, side=side, reason="risk_budget", trigger=reason)
                )
            if len(portfolio.fills) > previous_fills:
                if breakout_entry_stop_channel_reset:
                    awaiting_channel_reset = False
                    profit_reentry_after = None
                    if side == "sell" and reason == "signal":
                        if signal_time is None:
                            raise ValueError("채널 리셋의 매도 신호 시각이 없습니다")
                        signal_index = indices[signal_time] - 1
                        # Classify using the original sell signal, not the later fill candle.
                        awaiting_channel_reset = bars[signal_index].close >= min(
                            b.low for b in bars[signal_index - 48 : signal_index]
                        )
                        if awaiting_channel_reset and breakout_entry_stop_loss_reset:
                            entry_fill, exit_fill = portfolio.fills[-2:]
                            net_pnl = (
                                exit_fill.price * exit_fill.quantity
                                - exit_fill.fee
                                - entry_fill.price * entry_fill.quantity
                                - entry_fill.fee
                            )
                            awaiting_channel_reset = net_pnl <= ZERO
                            if breakout_confirmed_profit_reentry and net_pnl > ZERO:
                                profit_reentry_after = bar.open_time
                entry_time = bar.open_time if side == "buy" else None
                if breakout_failure_exit:
                    if side == "buy":
                        if signal_time is None:
                            raise ValueError("돌파 실패 청산의 진입 신호 시각이 없습니다")
                        # Signal close is the next bar's open; exclude the signal bar itself.
                        signal_index = indices[signal_time] - 1
                        entry_breakout_level = max(
                            b.high for b in bars[signal_index - 168 : signal_index]
                        )
                        if breakout_failure_buffer:
                            entry_breakout_level -= prior_mean_true_range(
                                bars[signal_index - 25 : signal_index + 1]
                            )
                    else:
                        entry_breakout_level = None
                if breakout_trailing_exit or breakout_entry_stop:
                    if side == "buy":
                        if signal_time is None:
                            raise ValueError("변동 폭 청산의 진입 신호 시각이 없습니다")
                        signal_index = indices[signal_time] - 1
                        entry_true_range = prior_mean_true_range(
                            bars[signal_index - 25 : signal_index + 1]
                        )
                        if long_entry_at is not None and signal_time == long_entry_at:
                            position_trend_lookback = 168
                            entry_fork_matched = True
                        elif breakout_entry_path_context:
                            position_trend_lookback = _entry_path_context(
                                bars[signal_index - 24 : signal_index + 1]
                            )
                        elif breakout_entry_context:
                            # Delayed execution must still use the original signal's history.
                            position_trend_lookback = _entry_trend_context(
                                bars[signal_index - 25 : signal_index + 1]
                            )
                        entry_stop_active = breakout_entry_stop
                        if breakout_entry_stop_floor or breakout_entry_stop_expansion:
                            slow_range = prior_mean_true_range(
                                bars[signal_index - 169 : signal_index + 1], lookback=168
                            )
                            if breakout_entry_stop_floor:
                                entry_true_range = max(entry_true_range, slow_range)
                            else:
                                entry_stop_active = entry_true_range > slow_range
                        trailing_peak = (
                            bar.open
                            if breakout_trailing_exit or breakout_entry_stop_profit_trail
                            else None
                        )
                        entry_reference = bar.open
                        if breakout_entry_stop_adaptive_trail:
                            entry_stop_threshold = bar.open - stop_multiple * entry_true_range
                    else:
                        entry_true_range = trailing_peak = entry_reference = None
                        position_trend_lookback = breakout_entry_stop_trend_lookback
                        entry_stop_active = False
                        entry_stop_threshold = None
                    entry_stop_breaches = 0
                    long_breaches = 0
                    decision_recorded = False
                    extend_position = False
                    checkpoint_at = None
                    checkpoint_reference = None
                    checkpoint_exit_due = False
                    extension_floor_breaches = 0
                    liquidation_exit_due = False
                if side == "sell":
                    last_exit = bar.open_time
            pending = None
        portfolio.mark(bar.open, bar.open_time, "open")
        exposure += int(portfolio.btc > 0)
        history.append(bar)
        if (breakout_trailing_exit or breakout_entry_stop_profit_trail) and portfolio.btc > 0:
            if trailing_peak is None:
                raise ValueError("고점 추적 청산의 체결 기준이 없습니다")
            trailing_peak = max(trailing_peak, bar.close)
            if breakout_entry_stop_profit_trail:
                if entry_true_range is None or entry_reference is None:
                    raise ValueError("상승 후 추적의 실제 매수 기준이 없습니다")
                distance = stop_multiple * entry_true_range
                threshold = entry_reference - distance
                if trailing_peak >= entry_reference + activation_multiple * entry_true_range:
                    if breakout_entry_stop_adaptive_trail:
                        distance = stop_multiple * max(
                            entry_true_range, prior_mean_true_range(history)
                        )
                    threshold = trailing_peak - distance
                if breakout_entry_stop_adaptive_trail:
                    if entry_stop_threshold is None:
                        raise ValueError("적응 추적의 실제 매수 청산선이 없습니다")
                    entry_stop_threshold = max(entry_stop_threshold, threshold)
                    threshold = entry_stop_threshold
                # Record this close against its contemporaneous threshold, even while pending.
                breach = entry_true_range > 0 and bar.close < threshold
                if breakout_entry_stop_trend_confirmation:
                    # Non-overlapping prior means, cross-multiplied to avoid division rounding.
                    rising = (
                        sum(b.close for b in history[-25:-1]) * position_trend_lookback
                        > sum(b.close for b in history[-25 - position_trend_lookback : -25]) * 24
                    )
                    breach = entry_true_range > 0 and (
                        bar.close < entry_reference - stop_multiple * entry_true_range
                        or (
                            trailing_peak
                            >= entry_reference + activation_multiple * entry_true_range
                            and bar.close < threshold
                            and not rising
                        )
                    )
                entry_stop_breaches = entry_stop_breaches + 1 if breach else 0
                if (
                    observe_exits
                    or breakout_exit_checkpoint
                    or breakout_exit_higher_low
                    or breakout_extension_floor
                ):
                    long_rising = (
                        sum(b.close for b in history[-25:-1]) * 168
                        > sum(b.close for b in history[-193:-25]) * 24
                    )
                    long_breach = entry_true_range > 0 and (
                        bar.close < entry_reference - 3 * entry_true_range
                        or (
                            trailing_peak >= entry_reference + 3 * entry_true_range
                            and bar.close < threshold
                            and not long_rising
                        )
                    )
                    long_breaches = long_breaches + 1 if long_breach else 0
        portfolio.mark(bar.close, bar.close_time, "close")
        if breakout_liquidation_buffer and portfolio.btc > ZERO and not portfolio.halted:
            buffered_price = max(
                ZERO, bar.close - (1 + extra_delay_bars) * prior_mean_true_range(history)
            )
            liquidation_equity = portfolio.cash + (
                portfolio.btc
                * buffered_price
                * (ONE - costs.sell_slippage)
                * (ONE - costs.sell_fee)
            )
            liquidation_exit_due = liquidation_exit_due or (
                liquidation_equity <= portfolio.peak * (ONE - policy.drawdown_limit)
            )
        if breakout_extension_floor and extend_position:
            if checkpoint_reference is None:
                raise ValueError("연장 가격 기준이 없습니다")
            extension_floor_breaches = (
                extension_floor_breaches + 1 if bar.close < checkpoint_reference else 0
            )
            checkpoint_exit_due = checkpoint_exit_due or extension_floor_breaches >= 2
        if portfolio.halted:
            # Risk replaces strategy orders, but an existing risk order keeps its due bar.
            if portfolio.btc <= 0:
                pending = None
            elif pending is None or pending[2] != "risk":
                pending = (index + 1 + extra_delay_bars, "sell", "risk", bar.close_time)
        elif active and pending is None:
            if checkpoint_at is not None and bar.close_time >= checkpoint_at:
                if checkpoint_reference is None:
                    raise ValueError("중간 점검의 원래 청산 결정 종가가 없습니다")
                checkpoint_exit_due = bar.close <= checkpoint_reference
                checkpoint_at = None
            signal = target(history, portfolio.btc > 0)
            if (
                (breakout_exit_checkpoint or breakout_exit_higher_low or breakout_extension_floor)
                and signal == "sell"
                and not decision_recorded
                and bar.close >= min(b.low for b in history[-49:-1])
            ):
                decision_recorded = True
                allow_extension = not breakout_exit_higher_low or (
                    min(b.low for b in history[-25:-1]) > min(b.low for b in history[-49:-25])
                )
                if long_breaches < breakout_entry_stop_confirmations and allow_extension:
                    extend_position = True
                    if breakout_exit_checkpoint:
                        checkpoint_at = bar.close_time + timedelta(hours=24)
                    if breakout_exit_checkpoint or breakout_extension_floor:
                        checkpoint_reference = bar.close
                    signal = target(history, True)
            if (
                observe_exits
                and signal == "sell"
                and not decision_recorded
                and bar.close >= min(b.low for b in history[-49:-1])
            ):
                if (
                    entry_true_range is None
                    or entry_reference is None
                    or trailing_peak is None
                    or entry_stop_threshold is None
                ):
                    raise ValueError("청산 분기 상태가 없습니다")
                prefix = {
                    "costs": costs.model_dump(mode="json"),
                    "capital": str(capital),
                    "delay": extra_delay_bars,
                    "fills": [f.model_dump(mode="json") for f in portfolio.fills],
                    "rejections": [r.model_dump(mode="json") for r in portfolio.rejections],
                    "curve": [p.model_dump(mode="json") for p in portfolio.curve],
                    "halted": portfolio.halted,
                    "last_exit": str(last_exit),
                    "awaiting_channel_reset": awaiting_channel_reset,
                    "entry_stop_active": entry_stop_active,
                }
                decision = ExitDecision(
                    signal_time=bar.close_time,
                    entry_fill=portfolio.fills[-1].model_copy(deep=True),
                    cash=portfolio.cash,
                    btc=portfolio.btc,
                    account_peak=portfolio.peak,
                    max_drawdown=portfolio.max_drawdown,
                    equity=portfolio.cash + portfolio.btc * bar.close,
                    entry_true_range=entry_true_range,
                    entry_reference=entry_reference,
                    trailing_peak=trailing_peak,
                    threshold=entry_stop_threshold,
                    short_breaches=entry_stop_breaches,
                    long_breaches=long_breaches,
                    prefix_sha256=hashlib.sha256(
                        json.dumps(prefix, sort_keys=True).encode()
                    ).hexdigest(),
                    candles_sha256=hashlib.sha256(
                        "".join(b.model_dump_json() + "\n" for b in history).encode()
                    ).hexdigest(),
                )
                if exit_decisions is not None:
                    exit_decisions.append(decision)
                decision_recorded = True
                if bar.close_time == extend_exit_at:
                    extend_position = fork_matched = True
                    signal = target(history, True)
            if signal:
                pending = (index + 1 + extra_delay_bars, signal, "signal", bar.close_time)
    if extend_exit_at is not None and not fork_matched:
        raise ValueError("청산 분기 시각이 최초 추가 청산 결정과 일치하지 않습니다")
    if long_entry_at is not None and not entry_fork_matched:
        raise ValueError("진입 분기 시각에 실제 매수가 없습니다")
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
