"""Run only against an explicitly configured disposable MariaDB database."""

import asyncio
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from alembic import command
from alembic.util.exc import CommandError
from pydantic import SecretStr
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.database.config import DatabaseSettings
from evergreen.database.connection import LOCK_NAME, locked_connection
from evergreen.database.migration import configuration, migrate, run_migrations
from evergreen.database.migrations.versions.v0001_execution import legacy_schema
from evergreen.trading.config import TradingSettings
from evergreen.trading.runtime import run
from evergreen.trading.state import Store, events, metadata, open_store
from evergreen.trading.upbit import Upbit


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
        await connection.execute(text("DROP TABLE IF EXISTS evergreen_alembic_version"))
    try:
        yield value
    finally:
        await value.dispose()


@pytest.mark.asyncio
async def test_mariadb_persistence_lock_identity_and_explicit_initialization(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(ValueError, match="최초 초기화"):
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
    monkeypatch.setattr("evergreen.trading.runtime.Upbit", factory)
    assert await run(config, initialize=True, once=True) == 0
    factory.assert_not_called()
    trader = AsyncMock()
    trader.tick.return_value = "no-signal"
    monkeypatch.setattr("evergreen.trading.runtime.Trader", Mock(return_value=trader))
    assert await run(config, initialize=False, once=True) == 0
    trader.tick.assert_awaited_once()
    sdk.close.assert_awaited_once()
    trader.tick.side_effect = RuntimeError("test failure")
    with pytest.raises(RuntimeError):
        await run(config, initialize=False, once=True)
    async with open_store(engine, config.identity):
        pass  # Worker failure released the DB lock.


@pytest.mark.asyncio
async def test_loop_cancellation_preserves_pending_and_releases_single_owner_lock(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = TradingSettings(
        live_enabled=True,
        access_key=SecretStr("test"),
        secret_key=SecretStr("test"),
        datasource_url=SecretStr(f"jdbc:mariadb://127.0.0.1:{engine.url.port}/evergreen_test"),
        datasource_username=SecretStr(engine.url.username or ""),
        datasource_password=SecretStr(engine.url.password or ""),
    )
    async with open_store(engine, config.identity, initialize=True):
        pass
    started = asyncio.Event()
    sdk = AsyncMock()
    factory = Mock(return_value=sdk)

    def trader(api: Upbit, store: Store, settings: TradingSettings) -> Mock:
        async def tick() -> str:
            state = await store.load()
            state.pending = {"identifier": "unconfirmed-intent"}
            await store.save(state, "intent")
            started.set()
            await asyncio.Event().wait()
            return "pending"

        return Mock(tick=tick)

    monkeypatch.setattr("evergreen.trading.runtime.Upbit", factory)
    monkeypatch.setattr("evergreen.trading.runtime.Trader", trader)
    task = asyncio.create_task(run(config, initialize=False, once=False))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        with pytest.raises(RuntimeError, match="다른"):
            await run(config, initialize=False, once=True)
        factory.assert_called_once()  # The second loop never reaches the exchange adapter.
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    sdk.close.assert_awaited_once()
    async with open_store(engine, config.identity) as store:
        assert (await store.load()).pending == {"identifier": "unconfirmed-intent"}


@pytest.mark.asyncio
async def test_schema_upgrade_is_serialized_idempotent_and_does_not_initialize_account(
    engine: AsyncEngine,
) -> None:
    assert await migrate(engine, "current") == ()
    results = await asyncio.gather(migrate(engine), migrate(engine))
    assert all(heads == ("0001_execution",) for heads in results)
    assert await migrate(engine) == ("0001_execution",)
    async with engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT COUNT(*) FROM evergreen_execution_state"))
        ).scalar_one() == 0
    async with locked_connection(engine):
        with pytest.raises(RuntimeError, match="잠금"):
            await migrate(engine, wait_seconds=0)


@pytest.mark.asyncio
async def test_legacy_baseline_preserves_order_state_and_audit(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(legacy_schema().create_all)
        await connection.execute(
            text("INSERT INTO evergreen_execution_state VALUES (1, :payload)"),
            {"payload": '{"identity":"test","pending":{"identifier":"preserve"},"halted":true}'},
        )
        await connection.execute(
            text(
                "INSERT INTO evergreen_execution_event (time,event,payload) VALUES ('old','intent','preserve')"
            )
        )
    with pytest.raises(SQLAlchemyError):
        await migrate(engine)  # Existing tables are never automatically stamped.
    assert await migrate(engine, "baseline") == ("0001_execution",)
    assert await migrate(engine) == ("0001_execution",)
    async with engine.connect() as connection:
        assert (
            "preserve"
            in (
                await connection.execute(text("SELECT payload FROM evergreen_execution_state"))
            ).scalar_one()
        )
        assert (
            await connection.execute(text("SELECT payload FROM evergreen_execution_event"))
        ).scalar_one() == "preserve"
    with pytest.raises(ValueError, match="이미"):
        await migrate(engine, "baseline")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ddl",
    [
        "DROP TABLE evergreen_execution_event",
        "ALTER TABLE evergreen_execution_event MODIFY event VARCHAR(20) NOT NULL",
        "ALTER TABLE evergreen_execution_event ENGINE=MyISAM",
        "ALTER TABLE evergreen_execution_state MODIFY id INT NOT NULL, DROP PRIMARY KEY",
        "ALTER TABLE evergreen_execution_event MODIFY id INT NOT NULL",
    ],
)
async def test_legacy_baseline_rejects_partial_or_different_schema(
    engine: AsyncEngine, ddl: str
) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(legacy_schema().create_all)
        await connection.execute(text(ddl))
    with pytest.raises(ValueError):
        await migrate(engine, "baseline")
    assert await migrate(engine, "current") == ()


@pytest.mark.asyncio
async def test_migration_runner_uses_existing_datasource_without_upbit_keys(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evergreen.database.__main__ import main

    config = DatabaseSettings(
        datasource_url=SecretStr(f"jdbc:mariadb://127.0.0.1:{engine.url.port}/evergreen_test"),
        datasource_username=SecretStr(engine.url.username or ""),
        datasource_password=SecretStr(engine.url.password or ""),
    )
    assert await run_migrations(config) == ("0001_execution",)
    monkeypatch.setattr("evergreen.database.__main__.load_dotenv", Mock())
    monkeypatch.setattr("evergreen.database.__main__.load_spring_config", Mock())
    monkeypatch.setattr("evergreen.database.__main__.DatabaseSettings", lambda: config)
    assert await asyncio.to_thread(main, ["current"]) == 0
    assert "0001_execution" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_unknown_revision_and_destructive_downgrade_are_rejected(engine: AsyncEngine) -> None:
    await migrate(engine)
    async with locked_connection(engine) as connection:

        def downgrade(sync_connection: Connection) -> None:
            config = configuration()
            config.attributes["connection"] = sync_connection
            command.downgrade(config, "base")

        with pytest.raises(RuntimeError, match="삭제 downgrade"):
            await connection.run_sync(downgrade)
    assert await migrate(engine, "current") == ("0001_execution",)
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE evergreen_alembic_version SET version_num='future'"))
    with pytest.raises(CommandError):
        await migrate(engine)
    assert await migrate(engine, "current") == ("future",)
