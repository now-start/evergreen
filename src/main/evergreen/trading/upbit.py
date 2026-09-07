"""Fixed-host Upbit adapter. POST is deliberately never retried."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

import ccxt.async_support as ccxt
from pydantic import BaseModel, Field, TypeAdapter

from evergreen.market import Candle, Nonnegative, Positive, UpbitCandle
from evergreen.trading.config import TradingSettings

Rate = Annotated[Decimal, Field(ge=0, lt=1, allow_inf_nan=False)]


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
    def __init__(self, settings: TradingSettings) -> None:
        self.settings = settings
        self.sdk = ccxt.upbit(
            {
                "apiKey": settings.access_key.get_secret_value(),
                "secret": settings.secret_key.get_secret_value(),
                "hostname": "api.upbit.com",
                "enableRateLimit": True,
                "timeout": 10000,
                "verbose": False,
                "aiohttp_trust_env": False,
                "options": {"maxRetriesOnFailure": 0},
            }
        )

    async def close(self) -> None:
        await self.sdk.close()

    async def chance(self) -> Chance:
        self.settings.require_credentials()
        return Chance.model_validate(
            await self.sdk.private_get_orders_chance({"market": "KRW-BTC"})
        )

    async def book(self) -> Book:
        books = TypeAdapter(list[Book]).validate_python(
            await self.sdk.public_get_orderbook({"markets": "KRW-BTC"})
        )
        if len(books) != 1:
            raise ValueError("단일 BTC 호가가 필요합니다")
        return books[0]

    async def candles(self, end: datetime, now: datetime) -> list[Candle]:
        records = TypeAdapter(list[UpbitCandle]).validate_python(
            await self.sdk.public_get_candles_minutes_unit(
                {"unit": 60, "market": "KRW-BTC", "count": 169, "to": end.isoformat()}
            )
        )
        return sorted((bar.candle(now) for bar in records), key=lambda bar: bar.open_time)

    async def has_open_orders(self) -> bool:
        self.settings.require_credentials()
        value = await self.sdk.private_get_orders_open({"market": "KRW-BTC", "limit": 1})
        if not isinstance(value, list):
            raise ValueError("대기 주문 응답이 잘못됐습니다")
        return bool(value)

    async def order(self, identifier: str) -> Order:
        self.settings.require_credentials()
        return Order.model_validate(await self.sdk.private_get_order({"identifier": identifier}))

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
        return Order.model_validate(await self.sdk.private_post_orders(params))
