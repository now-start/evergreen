"""Run only against an explicitly configured disposable MariaDB database."""

import os
from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.trading.__main__ import run
from evergreen.trading.config import TradingSettings
from evergreen.trading.state import LOCK_NAME, events, metadata, open_store


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.getenv("EVERGREEN_TEST_MARIADB_URL")
    if not url:
        pytest.skip("disposable MariaDB URL not configured")
    value = create_async_engine(url, poolclass=NullPool)
    # Refuse destructive fixture setup against arbitrary databases.
    if value.url.host != "127.0.0.1" or value.url.database != "evergreen_test":
        raise ValueError("only the local evergreen_test database is allowed")
    async with value.begin() as connection:
        await connection.run_sync(metadata.drop_all)
    try:
        yield value
    finally:
        await value.dispose()


@pytest.mark.asyncio
async def test_mariadb_persistence_lock_identity_and_explicit_initialization(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(SQLAlchemyError):
        async with open_store(engine, "account"):
            pass
    async with open_store(engine, "account", initialize=True) as store:
        state = await store.load()
        state.halted, state.peak = True, Decimal("123456.789")
        state.pending = {"identifier": "never-repost"}
        await store.save(state, "intent")
        with pytest.raises(RuntimeError, match="다른"):
            async with open_store(engine, "account"):
                pass
    async with open_store(engine, "account") as store:
        state = await store.load()
        assert state.halted and state.peak == Decimal("123456.789")
        assert state.pending == {"identifier": "never-repost"}
    with pytest.raises(ValueError, match="계정"):
        async with open_store(engine, "wrong-account"):
            pass
    with pytest.raises(IntegrityError):
        async with open_store(engine, "account", initialize=True):
            pass
    async with engine.connect() as connection:
        assert (await connection.execute(select(events.c.event))).scalars().all() == ["intent"]


@pytest.mark.asyncio
async def test_mariadb_lost_lock_blocks_state_operations(engine: AsyncEngine) -> None:
    async with open_store(engine, "account", initialize=True) as store:
        async with store.connection.begin():
            await store.connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME})
        for operation in (store.load, store.assert_owner):
            with pytest.raises(RuntimeError, match="잠금"):
                await operation()


@pytest.mark.asyncio
async def test_mariadb_audit_failure_rolls_back_state(engine: AsyncEngine) -> None:
    async with open_store(engine, "account", initialize=True) as store:
        state = await store.load()
        state.halted = True
        with pytest.raises(SQLAlchemyError):
            await store.save(state, "x" * 100)
        assert not (await store.load()).halted


@pytest.mark.asyncio
async def test_worker_initialization_and_cleanup_without_exchange_access(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = TradingSettings(
        access_key=SecretStr("test"),
        secret_key=SecretStr("test"),
        datasource_url=SecretStr(f"jdbc:mariadb://127.0.0.1:{engine.url.port}/evergreen_test"),
        datasource_username=SecretStr(engine.url.username or ""),
        datasource_password=SecretStr(engine.url.password or ""),
    )
    sdk = AsyncMock()
    factory = Mock(return_value=sdk)
    monkeypatch.setattr("evergreen.trading.__main__.Upbit", factory)
    assert await run(config, initialize=True, once=True) == 0
    factory.assert_not_called()
    trader = AsyncMock()
    trader.tick.return_value = "no-signal"
    monkeypatch.setattr("evergreen.trading.__main__.Trader", Mock(return_value=trader))
    assert await run(config, initialize=False, once=True) == 0
    trader.tick.assert_awaited_once()
    sdk.close.assert_awaited_once()
    trader.tick.side_effect = RuntimeError("test failure")
    with pytest.raises(RuntimeError):
        await run(config, initialize=False, once=True)
    async with open_store(engine, config.identity):
        pass  # Worker failure released the DB lock.
