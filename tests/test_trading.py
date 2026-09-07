import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from urllib.parse import unquote, urlencode

import ccxt
import pytest
from pydantic import SecretStr

from evergreen.trading.config import TradingSettings
from evergreen.trading.upbit import Upbit


def settings(**kwargs: object) -> TradingSettings:
    return TradingSettings.model_validate(
        {"access_key": SecretStr("test-access"), "secret_key": SecretStr("s" * 64), **kwargs}
    )


@pytest.mark.asyncio
async def test_sdk_signing_market_contract_and_no_post_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = Upbit(settings(live_enabled=True))
    seen = []
    params = {
        "market": "KRW-BTC",
        "side": "bid",
        "ord_type": "price",
        "price": "10000",
        "identifier": "test-order",
    }

    async def fetch(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> object:
        assert headers is not None and body is not None
        assert url == "https://api.upbit.com/v1/orders"
        assert method == "POST"
        payload = json.loads(body)
        assert payload == params
        encoded, token, signature = headers["Authorization"].removeprefix("Bearer ").split(".")
        decoded = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        algorithm = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["alg"]
        digest = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}[algorithm]
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(b"s" * 64, f"{encoded}.{token}".encode(), digest).digest()
            )
            .decode()
            .rstrip("=")
        )
        assert signature == expected
        assert (
            decoded["query_hash"]
            == hashlib.sha512(unquote(urlencode(payload)).encode()).hexdigest()
        )
        assert decoded["access_key"] == "test-access"
        seen.append(decoded["nonce"])
        raise ccxt.RequestTimeout("ambiguous")

    monkeypatch.setattr(api.sdk, "fetch", fetch)
    try:
        with pytest.raises(ccxt.RequestTimeout):
            await api.submit(params)
        assert len(seen) == 1
        assert api.sdk.options["maxRetriesOnFailure"] == 0
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"market": "KRW-ETH"},
        {
            "market": "KRW-BTC",
            "side": "bid",
            "ord_type": "price",
            "price": "NaN",
            "identifier": "x",
        },
    ],
)
async def test_invalid_order_never_reaches_sdk(
    params: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Upbit(settings(live_enabled=True))

    async def fail(*args: object) -> None:
        pytest.fail("invalid order must not reach SDK")

    monkeypatch.setattr(api.sdk, "private_post_orders", fail)
    try:
        with pytest.raises(ValueError):
            await api.submit(params)
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_disabled_submission_sends_no_request() -> None:
    api = Upbit(TradingSettings())
    try:
        with pytest.raises(ValueError):
            await api.submit({})
    finally:
        await api.close()


def test_existing_config_names_and_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPRING_DATASOURCE_URL", "jdbc:mariadb://db:3306/evergreen?useSSL=false")
    monkeypatch.setenv("SPRING_DATASOURCE_USERNAME", "db-user")
    monkeypatch.setenv("SPRING_DATASOURCE_PASSWORD", "private-db-password")
    value = settings()
    url = value.database_url
    assert (url.drivername, url.host, url.port, url.database) == (
        "mysql+asyncmy",
        "db",
        3306,
        "evergreen",
    )
    assert url.password == "private-db-password"  # noqa: S105 - test-only sentinel
    assert "private-db-password" not in repr(value)
    assert "test-access" not in value.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "jdbc:postgresql://db/evergreen",
        "jdbc:mariadb://db/evergreen?useSSL=true",
        "jdbc:mariadb://db/evergreen?unknown=true",
        "{cipher}encrypted",
        "jdbc:mariadb://user:password@db/evergreen",
    ],
)
def test_unsupported_database_settings_fail_closed(url: str) -> None:
    with pytest.raises(ValueError):
        _ = settings(
            datasource_url=SecretStr(url),
            datasource_username=SecretStr("u"),
            datasource_password=SecretStr("p"),
        ).database_url


@pytest.mark.parametrize("key", ["", "{cipher}not-decrypted"])
def test_missing_or_encrypted_credentials_fail(key: str) -> None:
    with pytest.raises(ValueError):
        TradingSettings(access_key=SecretStr(key), secret_key=SecretStr(key)).require_credentials()


@pytest.mark.asyncio
async def test_sdk_read_paths_and_response_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Upbit(settings())
    chance = AsyncMock(return_value={})
    orders = AsyncMock(return_value=[])
    order = AsyncMock(return_value={})
    book = AsyncMock(return_value=[])
    candles = AsyncMock(return_value=[])
    for name, response in (
        ("private_get_orders_chance", chance),
        ("private_get_orders_open", orders),
        ("private_get_order", order),
        ("public_get_orderbook", book),
        ("public_get_candles_minutes_unit", candles),
    ):
        monkeypatch.setattr(api.sdk, name, response)
    try:
        with pytest.raises(ValueError):
            await api.chance()
        chance.assert_awaited_once_with({"market": "KRW-BTC"})
        assert not await api.has_open_orders()
        orders.return_value = {"error": "bad"}
        with pytest.raises(ValueError):
            await api.has_open_orders()
        with pytest.raises(ValueError):
            await api.order("identifier")
        order.assert_awaited_once_with({"identifier": "identifier"})
        with pytest.raises(ValueError):
            await api.book()
        now = datetime(2026, 9, 7, tzinfo=UTC)
        assert await api.candles(now, now) == []
        candles.assert_awaited_once_with(
            {"unit": 60, "market": "KRW-BTC", "count": 169, "to": now.isoformat()}
        )
    finally:
        await api.close()
