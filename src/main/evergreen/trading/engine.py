"""Single-account breakout execution with write-ahead intents and reconciliation."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import NAMESPACE_URL, uuid5

from evergreen.market import validate_candles
from evergreen.observability import operation
from evergreen.strategies.breakout import target
from evergreen.strategies.buffer import ID, LIMIT, BufferState, IntentContext, entry_budget
from evergreen.trading import buffer_execution
from evergreen.trading.config import TradingSettings
from evergreen.trading.state import State, Store
from evergreen.trading.upbit import Book, Chance, Order, Upbit

STEP = Decimal(".00000001")
logger = logging.getLogger(__name__)


def _reject(reason: str, message: str) -> ValueError:
    # Only static reason codes belong in logs, never validation/HTTP payloads.
    logger.warning("event=trading_rejected reason=%s", reason)
    return ValueError(message)


def validate_chance(chance: Chance) -> None:
    market = chance.market
    if (
        market.state != "active"
        or market.bid.currency != "KRW"
        or market.ask.currency != "BTC"
        or chance.bid_account.currency != "KRW"
        or chance.ask_account.currency != "BTC"
        or "price" not in market.bid_types
        or "market" not in market.ask_types
    ):
        raise _reject("market_unavailable", "BTC/KRW 시장가 거래가 가능한 계정·마켓이 아닙니다")


def check_depth(book: Book, side: str, amount: Decimal, maximum: Decimal) -> None:
    """Estimate execution against visible depth; not a guaranteed market-order price bound."""
    units = book.orderbook_units
    prices = [row.ask_price if side == "bid" else row.bid_price for row in units]
    if units[0].bid_price > units[0].ask_price or prices != sorted(prices, reverse=side == "ask"):
        raise _reject("invalid_orderbook", "호가 정렬이 잘못됐습니다")
    remaining, cost, volume = amount, Decimal(0), Decimal(0)
    for row, price in zip(units, prices, strict=True):
        size = row.ask_size if side == "bid" else row.bid_size
        filled = min(size, remaining / price if side == "bid" else remaining)
        volume += filled
        cost += filled * price
        remaining -= filled * price if side == "bid" else filled
        if remaining <= 0:
            break
    if remaining > Decimal(".000000000001") or volume == 0:
        raise _reject("insufficient_depth", "전액 주문을 평가할 호가 잔량이 부족합니다")
    deviation = abs(cost / volume / prices[0] - 1)
    if deviation > maximum:
        raise _reject("slippage_exceeded", "예상 슬리피지가 제한을 초과합니다")


class Trader:
    def __init__(
        self,
        api: Upbit,
        store: Store,
        settings: TradingSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.api, self.store, self.settings, self.clock = api, store, settings, clock

    async def _book(self) -> Book:
        with operation(logger, "orderbook_fetch"):
            book = await self.api.book()
        age = self.clock().timestamp() - book.timestamp / 1000
        if not 0 <= age <= self.settings.max_quote_age_seconds:
            raise _reject("stale_quote", "호가가 오래됐거나 미래 시각입니다")
        return book

    def _check_order(self, state: State, order: Order) -> None:
        if state.pending is None or (order.identifier, order.side) != (
            state.pending["identifier"],
            state.pending["side"],
        ):
            raise _reject("order_identity_mismatch", "조회 주문과 저장된 주문 의도가 다릅니다")

    async def _reconcile(self, state: State) -> str:
        if state.pending is None:
            raise _reject("missing_order_intent", "복구할 주문이 없습니다")
        # A 404 or timeout is NOT evidence that POST never reached Upbit. Never resubmit.
        with operation(logger, "order_reconcile_lookup"):
            order = await self.api.order(state.pending["identifier"])
        self._check_order(state, order)
        if order.state in ("wait", "watch"):
            return "pending"
        if state.krw is None or state.btc is None or order.trades is None:
            raise _reject("missing_settlement_data", "주문 전 잔고 또는 최종 체결 내역이 없습니다")
        volume = sum((fill.volume for fill in order.trades), Decimal(0))
        funds = sum((fill.funds for fill in order.trades), Decimal(0))
        if volume != order.executed_volume:
            raise _reject("fill_volume_mismatch", "누적 체결 수량과 체결 내역이 다릅니다")
        direction = Decimal(1) if order.side == "bid" else Decimal(-1)
        expected_cash = state.krw - direction * funds - order.paid_fee
        expected_btc = state.btc + direction * volume
        with operation(logger, "settlement_account_fetch"):
            chance = await self.api.chance()
        validate_chance(chance)
        if chance.bid_account.locked or chance.ask_account.locked:
            raise _reject("settlement_locked", "종료 주문의 잠금 해제가 확인되지 않았습니다")
        if (
            abs(chance.bid_account.balance - expected_cash) > Decimal(".01")
            or abs(chance.ask_account.balance - expected_btc) > STEP
        ):
            state.halted = True
            await self.store.save(state, "settlement-mismatch")
            raise _reject(
                "settlement_mismatch",
                "주문 체결 후 예상 잔고와 실제 잔고가 다릅니다. 수동 확인 필요",
            )
        state.krw, state.btc = chance.bid_account.balance, chance.ask_account.balance
        if state.strategy == ID:
            buffer_execution.settled(state, order, self.clock())
        state.pending = None
        state.intent_context = None
        await self.store.save(state, "order-terminal", order.model_dump(mode="json"))
        logger.info(
            "event=order_reconciled order_sequence=%d state=%s", state.order_sequence, order.state
        )
        return "reconciled"

    async def tick(self) -> str:
        with operation(logger, "trading_cycle"):
            result = await self._tick()
        level = (
            logging.DEBUG
            if result in {"pending", "already-evaluated", "outside-signal-window", "halted"}
            else logging.INFO
        )
        logger.log(level, "event=trading_cycle_result result=%s", result)
        return result

    async def _tick(self) -> str:
        if not self.settings.live_enabled:
            raise _reject("live_disabled", "실거래 비활성화 상태입니다")
        with operation(logger, "execution_state_load"):
            saved = await self.store.load_for_start(self.settings.identity)
        state = (
            saved
            if saved is not None
            else State(
                identity=self.settings.identity,
                strategy=self.settings.strategy,
                buffer=BufferState() if self.settings.strategy == ID else None,
            )
        )
        if state.identity != self.settings.identity:
            raise _reject("account_identity_mismatch", "실행 계정과 DB 상태가 다릅니다")
        if state.pending is not None:
            return await self._reconcile(state)
        with operation(logger, "account_fetch"):
            chance = await self.api.chance()
        validate_chance(chance)
        with operation(logger, "open_orders_check"):
            has_open_orders = (
                chance.bid_account.locked
                or chance.ask_account.locked
                or await self.api.has_open_orders()
            )
        if has_open_orders:
            raise _reject(
                "existing_open_orders", "기존 대기 주문·잠금 잔고가 있어 거래를 중단합니다"
            )
        book = await self._book()
        bid = book.orderbook_units[0].bid_price
        cash, btc = chance.bid_account.balance, chance.ask_account.balance
        if state.krw is None or state.btc is None:
            if btc * bid >= chance.market.ask.min_total or (state.strategy == ID and btc > STEP):
                raise _reject(
                    "initial_position_exists",
                    "첫 실행은 원화 전용 계정으로 시작해야 합니다. 기존 BTC 자동 인수 금지",
                )
            state.krw, state.btc = cash, btc
        elif abs(cash - state.krw) > Decimal(".01") or abs(btc - state.btc) > STEP:
            state.halted = True
            await self.store.save(state, "unexpected-balance")
            raise _reject(
                "unexpected_balance", "외부 입출금·수동 거래로 잔고가 변경됐습니다. 수동 확인 필요"
            )
        equity = cash + btc * bid * (1 - chance.ask_fee)
        prior_peak = state.peak
        state.peak = max(state.peak, equity)
        if saved is None:
            await self.store.initialize(state)
            # The first cycle only establishes a verified baseline; it never submits an order.
            return "initialized"
        limit = min(self.settings.max_drawdown, LIMIT if state.strategy == ID else Decimal(".10"))
        if state.peak > 0 and equity <= state.peak * (1 - limit):
            if not state.halted:
                logger.warning("event=trading_halted reason=max_drawdown")
            state.halted = True
        await self.store.save(state, "valuation")
        holding = btc * bid >= chance.market.ask.min_total
        now = self.clock()
        if now.utcoffset() is None:
            raise _reject("invalid_clock", "실행 시각에 시간대가 필요합니다")
        hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if state.strategy != self.settings.strategy and btc <= STEP and not state.halted:
            previous = state.strategy
            state.strategy = self.settings.strategy
            state.buffer = BufferState(last_bar=hour) if state.strategy == ID else None
            state.intent_context = state.reserved_exit = None
            state.last_signal = hour.isoformat()
            await self.store.save(
                state, "strategy-transition", {"from": previous, "to": state.strategy}
            )
            logger.info("event=strategy_transition from=%s to=%s", previous, state.strategy)
            return "strategy-transitioned"
        if state.strategy == ID and (
            state.buffer is None or ((state.buffer.protection is not None) != (btc > STEP))
        ):
            raise _reject(
                "buffer_position_mismatch", "후보의 보유 상태와 보호 상태를 확인해야 합니다"
            )
        context: IntentContext | None = None
        reason = "risk" if state.halted else "signal"
        if state.halted:
            if not holding:
                return "halted"
            side = "ask"
            if state.strategy == ID:
                # Exchange fills can have second precision; use the evaluated hour as a lower bound.
                context = IntentContext(signal_time=hour, reason="risk")
                state.reserved_exit = context
        else:
            reserved = state.strategy == ID and state.reserved_exit is not None
            if state.last_signal == hour.isoformat() and not reserved:
                return "already-evaluated"
            if (
                not reserved
                and not 0 <= (now - hour).total_seconds() <= self.settings.max_signal_age_seconds
            ):
                return "outside-signal-window"
            with operation(logger, "candles_fetch"):
                candles = await self.api.candles(hour, now)
            ordered, quality = validate_candles(
                candles, hour - timedelta(hours=self.settings.candle_count), hour, as_of=now
            )
            if (
                not quality.valid
                or quality.duplicates
                or quality.incomplete
                or quality.outside_range
                or ordered != candles
            ):
                raise _reject("invalid_candles", "연속 확정 봉 품질 검사가 실패했습니다")
            if state.strategy == ID:
                signal, context = buffer_execution.signal(
                    state, candles, chance, self.settings.max_slippage, prior_peak=prior_peak
                )
                if state.peak > 0 and equity <= state.peak * (1 - limit):
                    state.halted = True
                if state.halted:
                    logger.warning("event=trading_halted reason=recovered_max_drawdown")
                    if btc > STEP:
                        signal = "sell"
                        context = IntentContext(signal_time=hour, reason="risk")
                        state.reserved_exit = context
                    else:
                        signal = None
                    reason = "risk"
                # Preserve a sell reservation even if later depth/freshness checks reject submission.
                await self.store.save(state, "strategy-evaluated")
            else:
                signal = target(candles, holding)
                if state.strategy != self.settings.strategy and signal == "buy":
                    signal = None
            if signal is None:
                state.last_signal = hour.isoformat()
                await self.store.save(state, "no-signal")
                return "no-signal"
            side = "bid" if signal == "buy" else "ask"
        logger.info(
            "event=trading_signal strategy=%s side=%s reason=%s", state.strategy, side, reason
        )
        if side == "bid":
            amount = (cash / (1 + chance.bid_fee) - 1).quantize(Decimal(1), rounding=ROUND_DOWN)
            minimum = chance.market.bid.min_total
            notional = amount
        else:
            amount = btc.quantize(STEP, rounding=ROUND_DOWN)
            minimum = chance.market.ask.min_total
            notional = amount * bid
        if not minimum <= notional <= chance.market.max_total or amount <= 0:
            raise _reject(
                "order_size_out_of_bounds", "전액 주문이 최소·최대 주문액 조건에 맞지 않습니다"
            )
        book = await self._book()
        check_depth(book, side, amount, self.settings.max_slippage)
        if side == "bid" and state.strategy == ID:
            if context is None or not entry_budget(
                cash,
                state.peak,
                book.orderbook_units[0].ask_price,
                context.entry_tr,
                chance.bid_fee,
                chance.ask_fee,
                self.settings.max_slippage,
                self.settings.max_slippage,
                STEP,
            ):
                state.last_signal = hour.isoformat()
                await self.store.save(state, "entry-budget-rejected")
                logger.info("event=trading_rejected reason=risk_budget strategy=%s", state.strategy)
                return "risk-budget-rejected"
        fresh_signal = reason == "signal" and not (state.strategy == ID and side == "ask")
        if (
            fresh_signal
            and (self.clock() - hour).total_seconds() > self.settings.max_signal_age_seconds
        ):
            raise _reject("stale_signal", "주문 준비 중 신호 유효 시간이 지났습니다")
        state.order_sequence += 1
        identifier = (
            "eg-"
            + uuid5(
                NAMESPACE_URL,
                f"{state.identity}:KRW-BTC:{state.strategy}:{hour.isoformat()}:{side}:{reason}:{state.order_sequence}",
            ).hex
        )
        state.pending = {
            "market": "KRW-BTC",
            "side": side,
            "ord_type": "price" if side == "bid" else "market",
            "price" if side == "bid" else "volume": format(amount, "f"),
            "identifier": identifier,
        }
        state.last_signal = hour.isoformat()
        state.intent_context = context
        # Commit intent BEFORE the network call. Any ambiguous result blocks new orders.
        await self.store.save(state, "order-intent")
        logger.info(
            "event=order_intent_committed order_sequence=%d side=%s", state.order_sequence, side
        )
        with operation(logger, "order_submit", level=logging.INFO):
            # Logging handlers may block: validate freshness after the start log too.
            await self.store.assert_owner()
            submission_time = self.clock()
            quote_age = submission_time.timestamp() - book.timestamp / 1000
            signal_age = (submission_time - hour).total_seconds()
            if not 0 <= quote_age <= self.settings.max_quote_age_seconds or (
                fresh_signal and not 0 <= signal_age <= self.settings.max_signal_age_seconds
            ):
                raise _reject(
                    "expired_before_submission",
                    "DB 저장 중 호가 또는 신호가 만료됐습니다. 주문 의도 확인 필요",
                )
            order = await self.api.submit(state.pending)
        self._check_order(state, order)
        await self.store.save(state, "order-accepted", order.model_dump(mode="json"))
        logger.info("event=order_accepted order_sequence=%d", state.order_sequence)
        return "submitted"
