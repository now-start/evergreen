"""Fixed-host Upbit adapter. POST is deliberately never retried."""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter
from upbit import AsyncUpbit

from evergreen.market import Candle, Nonnegative, Positive, UpbitCandle, utc_hour, validate_candles
from evergreen.trading.config import TradingSettings

Rate = Annotated[Decimal, Field(ge=0, lt=1, allow_inf_nan=False)]
logger = logging.getLogger(__name__)


class Account(BaseModel):
    currency: str
    balance: Nonnegative
    locked: Nonnegative


class MarketSide(BaseModel):
    currency: str
    min_total: Positive


class Market(BaseModel):
    id: Literal["KRW-BTC"]
    state: str
    bid: MarketSide
    ask: MarketSide
    bid_types: list[str]
    ask_types: list[str]
    max_total: Positive


class Chance(BaseModel):
    bid_fee: Rate
    ask_fee: Rate
    market: Market
    bid_account: Account
    ask_account: Account


class Level(BaseModel):
    ask_price: Positive
    bid_price: Positive
    ask_size: Nonnegative
    bid_size: Nonnegative


class Book(BaseModel):
    market: Literal["KRW-BTC"]
    timestamp: int = Field(gt=0)
    orderbook_units: list[Level] = Field(min_length=1)


class Fill(BaseModel):
    funds: Positive
    volume: Positive
    created_at: AwareDatetime | None = None


class Order(BaseModel):
    uuid: str = Field(min_length=1)
    identifier: str
    market: Literal["KRW-BTC"]
    side: Literal["bid", "ask"]
    state: Literal["wait", "watch", "done", "cancel"]
    executed_volume: Nonnegative
    paid_fee: Nonnegative
    trades: list[Fill] | None = None


class Upbit:
    def __init__(
        self, settings: TradingSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        # SDK options and HTTP query logs can expose private order identifiers.
        for name in ("upbit", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self.sdk = AsyncUpbit(
            access_key=settings.access_key.get_secret_value(),
            secret_key=settings.secret_key.get_secret_value(),
            base_url="https://api.upbit.com",
            environment="kr",
            max_retries=0,
            timeout=10,
            http_client=httpx.AsyncClient(
                transport=transport, trust_env=False, follow_redirects=False, timeout=10
            ),
        )

    async def close(self) -> None:
        await self.sdk.close()

    async def chance(self) -> Chance:
        self.settings.require_credentials()
        response = await self.sdk.orders.with_raw_response.retrieve_chance(market="KRW-BTC")
        return Chance.model_validate(await response.json())

    async def book(self) -> Book:
        response = await self.sdk.orderbooks.with_raw_response.list(markets="KRW-BTC")
        books = TypeAdapter(list[Book]).validate_python(await response.json())
        if len(books) != 1:
            raise ValueError("단일 BTC 호가가 필요합니다")
        return books[0]

    async def candles(
        self, end: datetime, now: datetime, *, start: datetime | None = None
    ) -> list[Candle]:
        """Read closed hours backwards; an explicit recovery range must be complete."""
        end = utc_hour(end)
        if start is not None:
            start = utc_hour(start)
            if not start < end <= now:
                raise ValueError("복구 조회는 확정된 시간봉 범위여야 합니다")
        cursor, pages = end, 0
        result: list[Candle] = []
        while True:
            count = (
                self.settings.candle_count
                if start is None
                else min(200, int((cursor - start).total_seconds() // 3600))
            )
            response = await self.sdk.candles.with_raw_response.list_minutes(
                60, market="KRW-BTC", count=count, to=cursor.isoformat()
            )
            records = TypeAdapter(list[UpbitCandle]).validate_python(await response.json())
            batch = sorted((bar.candle(now) for bar in records), key=lambda bar: bar.open_time)
            pages += 1
            if start is None:
                return batch
            page_start = cursor - timedelta(hours=count)
            ordered, quality = validate_candles(batch, page_start, cursor, as_of=now)
            if (
                not quality.valid
                or quality.duplicates
                or quality.incomplete
                or quality.outside_range
                or ordered != batch
            ):
                logger.error(
                    "event=candle_recovery status=failed reason=invalid_page page=%d", pages
                )
                raise ValueError("복구 캔들 페이지의 연속성·확정 여부 검사가 실패했습니다")
            result.extend(batch)
            cursor = page_start
            if cursor == start:
                logger.info(
                    "event=candle_recovery status=completed pages=%d bars=%d", pages, len(result)
                )
                return sorted(result, key=lambda bar: bar.open_time)
            # Share the candle API budget; cancellation must leave the durable cursor untouched.
            await asyncio.sleep(0.12)

    async def has_open_orders(self) -> bool:
        self.settings.require_credentials()
        response = await self.sdk.orders.with_raw_response.list_open(market="KRW-BTC", limit=1)
        value = await response.json()
        if not isinstance(value, list):
            raise ValueError("대기 주문 응답이 잘못됐습니다")
        return bool(value)

    async def order(self, identifier: str) -> Order:
        self.settings.require_credentials()
        response = await self.sdk.orders.with_raw_response.retrieve(identifier=identifier)
        return Order.model_validate(await response.json())

    async def submit(self, params: dict[str, str]) -> Order:
        if not self.settings.live_enabled:
            raise ValueError("실거래가 비활성화되어 있습니다")
        self.settings.require_credentials()
        side = params.get("side")
        required = {
            "market",
            "side",
            "ord_type",
            "identifier",
            "price" if side == "bid" else "volume",
        }
        if (
            set(params) != required
            or params.get("market") != "KRW-BTC"
            or (side, params.get("ord_type")) not in (("bid", "price"), ("ask", "market"))
        ):
            raise ValueError("BTC 전용 시장가 주문 형식이 필요합니다")
        amount = Decimal(params["price" if side == "bid" else "volume"])
        if not amount.is_finite() or amount <= 0 or not 1 <= len(params["identifier"]) <= 64:
            raise ValueError("주문 수량·식별자가 잘못됐습니다")
        if side == "bid":
            response = await self.sdk.orders.with_raw_response.create(
                market="KRW-BTC",
                side="bid",
                ord_type="price",
                price=params["price"],
                identifier=params["identifier"],
            )
        else:
            response = await self.sdk.orders.with_raw_response.create(
                market="KRW-BTC",
                side="ask",
                ord_type="market",
                volume=params["volume"],
                identifier=params["identifier"],
            )
        return Order.model_validate(await response.json())
