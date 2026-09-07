"""Single-account breakout execution with write-ahead intents and reconciliation."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import NAMESPACE_URL, uuid5

from evergreen.market import validate_candles
from evergreen.strategies.breakout import target
from evergreen.trading.config import TradingSettings
from evergreen.trading.state import State, Store
from evergreen.trading.upbit import Book, Chance, Order, Upbit

STEP = Decimal(".00000001")


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
        raise ValueError("BTC/KRW 시장가 거래가 가능한 계정·마켓이 아닙니다")


def check_depth(book: Book, side: str, amount: Decimal, maximum: Decimal) -> None:
    """Estimate execution against visible depth; not a guaranteed market-order price bound."""
    units = book.orderbook_units
    prices = [row.ask_price if side == "bid" else row.bid_price for row in units]
    if units[0].bid_price > units[0].ask_price or prices != sorted(prices, reverse=side == "ask"):
        raise ValueError("호가 정렬이 잘못됐습니다")
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
        raise ValueError("전액 주문을 평가할 호가 잔량이 부족합니다")
    deviation = abs(cost / volume / prices[0] - 1)
    if deviation > maximum:
        raise ValueError("예상 슬리피지가 제한을 초과합니다")


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
        book = await self.api.book()
        age = self.clock().timestamp() - book.timestamp / 1000
        if not 0 <= age <= self.settings.max_quote_age_seconds:
            raise ValueError("호가가 오래됐거나 미래 시각입니다")
        return book

    def _check_order(self, state: State, order: Order) -> None:
        if state.pending is None or (order.identifier, order.side) != (
            state.pending["identifier"],
            state.pending["side"],
        ):
            raise ValueError("조회 주문과 저장된 주문 의도가 다릅니다")

    async def _reconcile(self, state: State) -> str:
        if state.pending is None:
            raise ValueError("복구할 주문이 없습니다")
        # A 404 or timeout is NOT evidence that POST never reached Upbit. Never resubmit.
        order = await self.api.order(state.pending["identifier"])
        self._check_order(state, order)
        if order.state in ("wait", "watch"):
            return "pending"
        if state.krw is None or state.btc is None or order.trades is None:
            raise ValueError("주문 전 잔고 또는 최종 체결 내역이 없습니다")
        volume = sum((fill.volume for fill in order.trades), Decimal(0))
        funds = sum((fill.funds for fill in order.trades), Decimal(0))
        if volume != order.executed_volume:
            raise ValueError("누적 체결 수량과 체결 내역이 다릅니다")
        direction = Decimal(1) if order.side == "bid" else Decimal(-1)
        expected_cash = state.krw - direction * funds - order.paid_fee
        expected_btc = state.btc + direction * volume
        chance = await self.api.chance()
        validate_chance(chance)
        if chance.bid_account.locked or chance.ask_account.locked:
            raise ValueError("종료 주문의 잠금 해제가 확인되지 않았습니다")
        if (
            abs(chance.bid_account.balance - expected_cash) > Decimal(".01")
            or abs(chance.ask_account.balance - expected_btc) > STEP
        ):
            state.halted = True
            await self.store.save(state, "settlement-mismatch")
            raise ValueError("주문 체결 후 예상 잔고와 실제 잔고가 다릅니다. 수동 확인 필요")
        state.krw, state.btc = chance.bid_account.balance, chance.ask_account.balance
        state.pending = None
        await self.store.save(state, "order-terminal", order.model_dump(mode="json"))
        return "reconciled"

    async def tick(self) -> str:
        if not self.settings.live_enabled:
            raise ValueError("실거래 비활성화 상태입니다")
        state = await self.store.load()
        if state.identity != self.settings.identity:
            raise ValueError("실행 계정과 DB 상태가 다릅니다")
        if state.pending is not None:
            return await self._reconcile(state)
        chance = await self.api.chance()
        validate_chance(chance)
        if (
            chance.bid_account.locked
            or chance.ask_account.locked
            or await self.api.has_open_orders()
        ):
            raise ValueError("기존 대기 주문·잠금 잔고가 있어 거래를 중단합니다")
        book = await self._book()
        bid = book.orderbook_units[0].bid_price
        cash, btc = chance.bid_account.balance, chance.ask_account.balance
        if state.krw is None or state.btc is None:
            if btc * bid >= chance.market.ask.min_total:
                raise ValueError(
                    "첫 실행은 원화 전용 계정으로 시작해야 합니다. 기존 BTC 자동 인수 금지"
                )
            state.krw, state.btc = cash, btc
        elif abs(cash - state.krw) > Decimal(".01") or abs(btc - state.btc) > STEP:
            state.halted = True
            await self.store.save(state, "unexpected-balance")
            raise ValueError("외부 입출금·수동 거래로 잔고가 변경됐습니다. 수동 확인 필요")
        equity = cash + btc * bid * (1 - chance.ask_fee)
        state.peak = max(state.peak, equity)
        if state.peak > 0 and equity <= state.peak * (1 - self.settings.max_drawdown):
            state.halted = True
        await self.store.save(state, "valuation")
        holding = btc * bid >= chance.market.ask.min_total
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("실행 시각에 시간대가 필요합니다")
        hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        reason = "risk" if state.halted else "signal"
        if state.halted:
            if not holding:
                return "halted"
            side = "ask"
        else:
            if state.last_signal == hour.isoformat():
                return "already-evaluated"
            if not 0 <= (now - hour).total_seconds() <= self.settings.max_signal_age_seconds:
                return "outside-signal-window"
            candles = await self.api.candles(hour, now)
            ordered, quality = validate_candles(
                candles, hour - timedelta(hours=169), hour, as_of=now
            )
            if (
                not quality.valid
                or quality.duplicates
                or quality.incomplete
                or quality.outside_range
                or ordered != candles
            ):
                raise ValueError("169개 연속 확정 봉 품질 검사가 실패했습니다")
            signal = target(candles, holding)
            if signal is None:
                state.last_signal = hour.isoformat()
                await self.store.save(state, "no-signal")
                return "no-signal"
            side = "bid" if signal == "buy" else "ask"
        if side == "bid":
            amount = (cash / (1 + chance.bid_fee) - 1).quantize(Decimal(1), rounding=ROUND_DOWN)
            minimum = chance.market.bid.min_total
            notional = amount
        else:
            amount = btc.quantize(STEP, rounding=ROUND_DOWN)
            minimum = chance.market.ask.min_total
            notional = amount * bid
        if not minimum <= notional <= chance.market.max_total or amount <= 0:
            raise ValueError("전액 주문이 최소·최대 주문액 조건에 맞지 않습니다")
        book = await self._book()
        check_depth(book, side, amount, self.settings.max_slippage)
        if (
            reason == "signal"
            and (self.clock() - hour).total_seconds() > self.settings.max_signal_age_seconds
        ):
            raise ValueError("주문 준비 중 신호 유효 시간이 지났습니다")
        state.order_sequence += 1
        identifier = (
            "eg-"
            + uuid5(
                NAMESPACE_URL,
                f"{state.identity}:KRW-BTC:breakout-v1:{hour.isoformat()}:{side}:{reason}:{state.order_sequence}",
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
        # Commit intent BEFORE the network call. Any ambiguous result blocks new orders.
        await self.store.save(state, "order-intent")
        await self.store.assert_owner()
        submission_time = self.clock()
        quote_age = submission_time.timestamp() - book.timestamp / 1000
        signal_age = (submission_time - hour).total_seconds()
        if not 0 <= quote_age <= self.settings.max_quote_age_seconds or (
            reason == "signal" and not 0 <= signal_age <= self.settings.max_signal_age_seconds
        ):
            raise ValueError("DB 저장 중 호가 또는 신호가 만료됐습니다. 주문 의도 확인 필요")
        order = await self.api.submit(state.pending)
        self._check_order(state, order)
        await self.store.save(state, "order-accepted", order.model_dump(mode="json"))
        return "submitted"
