import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import unquote, urlencode

import httpx
import pytest
from pydantic import SecretStr
from upbit import APITimeoutError

from evergreen.trading.config import TradingSettings
from evergreen.trading.upbit import Upbit


def settings(**kwargs: object) -> TradingSettings:
    return TradingSettings.model_validate(
        {"access_key": SecretStr("test-access"), "secret_key": SecretStr("s" * 64), **kwargs}
    )


@pytest.mark.asyncio
async def test_sdk_signing_market_contract_and_no_post_retry() -> None:
    seen = []
    params = {
        "market": "KRW-BTC",
        "side": "bid",
        "ord_type": "price",
        "price": "10000",
        "identifier": "test-order",
    }

    def fetch(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.upbit.com/v1/orders"
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload == params
        encoded, token, signature = (
            request.headers["Authorization"].removeprefix("Bearer ").split(".")
        )
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
        raise httpx.ReadTimeout("ambiguous", request=request)

    api = Upbit(settings(live_enabled=True), transport=httpx.MockTransport(fetch))
    try:
        with pytest.raises(APITimeoutError):
            await api.submit(params)
        assert len(seen) == 1
        assert api.sdk.max_retries == 0
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
async def test_invalid_order_never_reaches_sdk(params: dict[str, str]) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid order must not reach SDK")

    api = Upbit(settings(live_enabled=True), transport=httpx.MockTransport(fail))
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
async def test_sdk_read_paths_and_response_validation() -> None:
    responses: dict[str, object] = {
        "/v1/orders/chance": {},
        "/v1/orders/open": [],
        "/v1/order": {},
        "/v1/orderbook": [],
        "/v1/candles/minutes/60": [],
    }
    seen: list[httpx.Request] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=responses[request.url.path])

    api = Upbit(settings(), transport=httpx.MockTransport(fetch))
    try:
        with pytest.raises(ValueError):
            await api.chance()
        assert dict(seen[-1].url.params) == {"market": "KRW-BTC"}
        assert not await api.has_open_orders()
        assert dict(seen[-1].url.params) == {"market": "KRW-BTC", "limit": "1"}
        responses["/v1/orders/open"] = [{"uuid": "pending"}]
        assert await api.has_open_orders()
        responses["/v1/orders/open"] = {"error": "bad"}
        with pytest.raises(ValueError):
            await api.has_open_orders()
        with pytest.raises(ValueError):
            await api.order("identifier")
        assert dict(seen[-1].url.params) == {"identifier": "identifier"}
        with pytest.raises(ValueError):
            await api.book()
        now = datetime(2026, 9, 7, tzinfo=UTC)
        assert await api.candles(now, now) == []
        assert dict(seen[-1].url.params) == {
            "market": "KRW-BTC",
            "count": "169",
            "to": now.isoformat(),
        }
    finally:
        await api.close()
