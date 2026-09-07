"""MariaDB write-ahead order state guarded by a dedicated session-level lock."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from evergreen.market import Nonnegative

metadata = MetaData()
states = Table(
    "evergreen_execution_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("payload", Text, nullable=False),
    mysql_engine="InnoDB",
)
events = Table(
    "evergreen_execution_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("time", String(40), nullable=False),
    Column("event", String(40), nullable=False),
    Column("payload", Text, nullable=False),
    mysql_engine="InnoDB",
)
LOCK_NAME = "evergreen:execution:KRW-BTC"


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str
    peak: Nonnegative = Decimal(0)
    halted: bool = False
    last_signal: str | None = None
    krw: Nonnegative | None = None
    btc: Nonnegative | None = None
    pending: dict[str, str] | None = None
    order_sequence: int = Field(default=0, ge=0)


class Store:
    def __init__(self, connection: AsyncConnection, owner: int) -> None:
        self.connection, self.owner = connection, owner

    async def _check_owner(self) -> None:
        value = (
            await self.connection.execute(
                text("SELECT IS_USED_LOCK(:name) = CONNECTION_ID() AND CONNECTION_ID() = :owner"),
                {"name": LOCK_NAME, "owner": self.owner},
            )
        ).scalar_one()
        if value != 1:
            raise RuntimeError("MariaDB 실행 잠금을 잃었습니다")

    async def assert_owner(self) -> None:
        async with self.connection.begin():
            await self._check_owner()

    async def load(self) -> State:
        async with self.connection.begin():
            await self._check_owner()
            row = (
                await self.connection.execute(select(states.c.payload).where(states.c.id == 1))
            ).scalar_one_or_none()
        if row is None:
            raise ValueError("실행 상태가 없습니다. 명시적인 최초 초기화가 필요합니다")
        return State.model_validate_json(row)

    async def save(self, state: State, event: str, detail: dict[str, object] | None = None) -> None:
        async with self.connection.begin():
            await self._check_owner()
            result = await self.connection.execute(
                update(states).where(states.c.id == 1).values(payload=state.model_dump_json())
            )
            if result.rowcount != 1:
                raise RuntimeError("실행 상태 행이 사라졌습니다")
            await self.connection.execute(
                insert(events).values(
                    time=datetime.now(UTC).isoformat(),
                    event=event,
                    payload=json.dumps({"state": state.model_dump(mode="json"), "detail": detail}),
                )
            )


@asynccontextmanager
async def open_store(
    engine: AsyncEngine, identity: str, *, initialize: bool = False
) -> AsyncIterator[Store]:
    # Keep this exact connection alive. Never reconnect and silently regain ownership.
    async with engine.connect() as connection:
        locked = (
            await connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK_NAME})
        ).scalar_one()
        await connection.commit()
        if locked != 1:
            raise RuntimeError("다른 거래 실행기가 MariaDB 잠금을 보유하고 있습니다")
        try:
            owner = int((await connection.execute(text("SELECT CONNECTION_ID()"))).scalar_one())
            await connection.commit()
            if initialize:
                async with connection.begin():
                    await connection.run_sync(metadata.create_all)
                    await connection.execute(
                        insert(states).values(
                            id=1, payload=State(identity=identity).model_dump_json()
                        )
                    )
            store = Store(connection, owner)
            if (await store.load()).identity != identity:
                raise ValueError("DB에 저장된 계정 키가 다릅니다")
            yield store
        finally:
            if not connection.invalidated:
                await connection.rollback()
                await connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME})
                await connection.commit()
