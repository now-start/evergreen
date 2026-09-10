"""MariaDB write-ahead order state guarded by a dedicated session-level lock."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from evergreen.database.connection import LOCK_NAME, locked_connection
from evergreen.database.migration import apply
from evergreen.market import Nonnegative
from evergreen.observability import operation
from evergreen.strategies.buffer import BufferState, ExecutionStrategy, IntentContext

logger = logging.getLogger(__name__)

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
    strategy: ExecutionStrategy = "breakout-v1"
    buffer: BufferState | None = None
    intent_context: IntentContext | None = None
    reserved_exit: IntentContext | None = None


class StateUnavailable(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        logger.error("event=execution_state_blocked reason=%s", reason)
        super().__init__("실행 상태를 복구하거나 최초 초기화 승인을 확인해야 합니다")


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
            logger.error("event=execution_lock_lost")
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
            logger.error("event=execution_state_missing")
            raise ValueError("실행 상태가 없습니다. 명시적인 최초 초기화가 필요합니다")
        return State.model_validate_json(row)

    async def _require_approval(self, identity: str) -> None:
        # Called inside the owner's transaction. Any other history means recovery, not setup.
        existing = (await self.connection.execute(select(states.c.id).limit(1))).first()
        history = (
            await self.connection.execute(select(events.c.event, events.c.payload).limit(2))
        ).all()
        if existing is not None:
            raise StateUnavailable("state_already_exists")
        if not history:
            raise StateUnavailable("initialization_approval_required")
        if len(history) != 1 or history[0].event != "initialization-approved":
            raise StateUnavailable("execution_state_recovery_required")
        try:
            approval = json.loads(history[0].payload)
        except ValueError:
            raise StateUnavailable("execution_state_recovery_required") from None
        if approval != {"identity": identity}:
            raise StateUnavailable("initialization_account_mismatch")

    async def approve_initialization(self, identity: str) -> None:
        async with self.connection.begin():
            await self._check_owner()
            existing = (await self.connection.execute(select(states.c.id).limit(1))).first()
            history = (await self.connection.execute(select(events.c.id).limit(1))).first()
            if existing is not None or history is not None:
                raise StateUnavailable("initialization_requires_empty_history")
            await self.connection.execute(
                insert(events).values(
                    time=datetime.now(UTC).isoformat(),
                    event="initialization-approved",
                    payload=json.dumps({"identity": identity}),
                )
            )
        logger.info("event=execution_initialization_approved")

    async def load_for_start(self, identity: str) -> State | None:
        async with self.connection.begin():
            await self._check_owner()
            row = (
                await self.connection.execute(select(states.c.payload).where(states.c.id == 1))
            ).scalar_one_or_none()
            if row is None:
                await self._require_approval(identity)
                return None
        state = State.model_validate_json(row)
        if state.identity != identity:
            logger.error("event=execution_account_mismatch")
            raise ValueError("DB에 저장된 계정 키가 다릅니다")
        return state

    async def initialize(self, state: State) -> None:
        async with self.connection.begin():
            await self._check_owner()
            await self._require_approval(state.identity)
            await self.connection.execute(
                insert(states).values(id=1, payload=state.model_dump_json())
            )
            await self.connection.execute(
                insert(events).values(
                    time=datetime.now(UTC).isoformat(),
                    event="initialized",
                    payload=json.dumps({"state": state.model_dump(mode="json"), "detail": None}),
                )
            )
        logger.info("event=execution_state_initialized")

    async def save(self, state: State, event: str, detail: dict[str, object] | None = None) -> None:
        async with self.connection.begin():
            await self._check_owner()
            result = await self.connection.execute(
                update(states).where(states.c.id == 1).values(payload=state.model_dump_json())
            )
            if result.rowcount != 1:
                logger.error("event=execution_state_missing")
                raise RuntimeError("실행 상태 행이 사라졌습니다")
            await self.connection.execute(
                insert(events).values(
                    time=datetime.now(UTC).isoformat(),
                    event=event,
                    payload=json.dumps({"state": state.model_dump(mode="json"), "detail": detail}),
                )
            )
        logger.log(
            logging.DEBUG if event == "valuation" else logging.INFO,
            "event=execution_state_committed transition=%s order_sequence=%d",
            event,
            state.order_sequence,
        )


@asynccontextmanager
async def open_store(
    engine: AsyncEngine, identity: str, *, initialize: bool = False
) -> AsyncIterator[Store]:
    async with locked_connection(engine) as connection:
        with operation(logger, "execution_schema_apply", level=logging.INFO):
            heads = await connection.run_sync(apply)
            await connection.commit()
        logger.info("event=database_revision action=upgrade revision=%s", ",".join(heads) or "base")
        owner = int((await connection.execute(text("SELECT CONNECTION_ID()"))).scalar_one())
        await connection.commit()
        store = Store(connection, owner)
        if initialize:
            await store.approve_initialization(identity)
        else:
            await store.load_for_start(identity)
        yield store
