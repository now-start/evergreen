import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from upbit import APIStatusError

from evergreen.trading.config import TradingSettings
from evergreen.trading.upbit import Upbit


@pytest.mark.asyncio
async def test_official_sdk() -> None:
    api = Upbit(TradingSettings())
    try:
        assert type(api.sdk).__name__ == "AsyncUpbit"
    finally:
        await api.close()


def live_settings() -> TradingSettings:
    return TradingSettings(
        live_enabled=True,
        access_key=SecretStr("test-access"),
        secret_key=SecretStr("s" * 64),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["bid", "ask"])
async def test_submit_and_identifier_lookup_preserve_decimal_strings(
    side: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("UPBIT_BASE_URL", "https://untrusted.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:8080")
    caplog.set_level(logging.DEBUG, logger="upbit")
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="httpcore")
    amount = "12345678.12345678" if side == "bid" else "0.12345678"
    params = {
        "market": "KRW-BTC",
        "side": side,
        "ord_type": "price" if side == "bid" else "market",
        "price" if side == "bid" else "volume": amount,
        "identifier": "test-private-identifier",
    }
    payload = {
        "uuid": "test-uuid",
        "identifier": params["identifier"],
        "market": "KRW-BTC",
        "side": side,
        "state": "done",
        "executed_volume": "0.12345678",
        "paid_fee": "123.123456789012345678",
        "trades": [{"funds": "12345678.123456789012345678", "volume": "0.12345678"}],
    }
    seen: list[httpx.Request] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.upbit.com"
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 10
        )
        if request.method == "POST":
            assert request.url.path == "/v1/orders"
            assert json.loads(request.content) == params
        else:
            assert request.url.path == "/v1/order"
            assert dict(request.url.params) == {"identifier": params["identifier"]}
        return httpx.Response(200, json=payload)

    api = Upbit(live_settings(), transport=httpx.MockTransport(fetch))
    try:
        submitted = await api.submit(params)
        assert submitted == await api.order(params["identifier"])
        assert submitted.paid_fee == Decimal("123.123456789012345678")
        assert submitted.trades is not None
        assert submitted.trades[0].funds == Decimal("12345678.123456789012345678")
        assert len(seen) == 2
        assert "test-access" not in caplog.text
        assert "test-private-identifier" not in caplog.text
        assert seen[0].headers["Authorization"] not in caplog.text
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [307, 401, 408, 409, 429, 500])
async def test_post_errors_never_retry_or_follow_redirect(status: int) -> None:
    seen: list[httpx.Request] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://untrusted.invalid/orders"},
            json={"error": {"name": "test_error", "message": "failed"}},
        )

    api = Upbit(live_settings(), transport=httpx.MockTransport(fetch))
    try:
        with pytest.raises(APIStatusError):
            await api.submit(
                {
                    "market": "KRW-BTC",
                    "side": "bid",
                    "ord_type": "price",
                    "price": "10000",
                    "identifier": "test-order",
                }
            )
        assert len(seen) == 1
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_valid_read_responses_and_chronological_candles() -> None:
    responses: dict[str, object] = {
        "/v1/orders/chance": {
            "bid_fee": "0.0005",
            "ask_fee": "0.0005",
            "market": {
                "id": "KRW-BTC",
                "state": "active",
                "max_total": "1000000000",
                "bid": {"currency": "KRW", "min_total": "5000"},
                "ask": {"currency": "BTC", "min_total": "5000"},
                "bid_types": ["price"],
                "ask_types": ["market"],
            },
            "bid_account": {"currency": "KRW", "balance": "12345678.12345678", "locked": "0"},
            "ask_account": {"currency": "BTC", "balance": "0.12345678", "locked": "0"},
        },
        "/v1/orderbook": [
            {
                "market": "KRW-BTC",
                "timestamp": 1788829200000,
                "orderbook_units": [
                    {
                        "ask_price": 101,
                        "bid_price": 100,
                        "ask_size": 1,
                        "bid_size": 2,
                    }
                ],
            }
        ],
        "/v1/candles/minutes/60": [
            {
                "market": "KRW-BTC",
                "unit": 60,
                "candle_date_time_utc": f"2026-09-08T0{hour}:00:00",
                "opening_price": 100,
                "high_price": 110,
                "low_price": 90,
                "trade_price": 105,
                "candle_acc_trade_volume": 1,
                "candle_acc_trade_price": 105,
            }
            for hour in (2, 1)
        ],
    }

    def fetch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path])

    api = Upbit(live_settings(), transport=httpx.MockTransport(fetch))
    try:
        chance = await api.chance()
        assert chance.bid_account.balance == Decimal("12345678.12345678")
        assert chance.bid_fee == Decimal("0.0005")
        assert (await api.book()).orderbook_units[0].ask_price == Decimal(101)
        now = datetime(2026, 9, 8, 3, tzinfo=UTC)
        bars = await api.candles(now, now)
        assert [bar.open_time.hour for bar in bars] == [1, 2]
        assert all(bar.fetched_at == now for bar in bars)
    finally:
        await api.close()
