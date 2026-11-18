"""Import the small, verified v2 snapshot into an empty PostgreSQL database."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.database.config import DatabaseSettings
from evergreen.database.connection import locked_connection
from evergreen.database.migration import apply
from evergreen.platform.config import PlatformSettings, load_spring_config
from evergreen.trading.performance import Ledger
from evergreen.trading.state import State, events, states

KEEP_EVENTS = frozenset(
    {
        "initialization-approved",
        "initialized",
        "cashflow-reconciled",
        "order-intent",
        "order-accepted",
        "order-terminal",
        "strategy-transition",
    }
)
FINANCIAL_EVENTS = {"initialized", "order-terminal", "cashflow-reconciled"}


class StateRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Literal[1]
    payload: str = Field(repr=False)


class EventRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int = Field(gt=0, lt=2**63 - 1)
    time: str = Field(min_length=1, max_length=40)
    event: str = Field(min_length=1, max_length=40)
    payload: str = Field(repr=False)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal[1]
    source_revision: Literal["0002_remove_key_identity"]
    source_max_id: int = Field(ge=0, lt=2**63 - 1)
    source_next_id: int = Field(gt=0, lt=2**63)
    state: StateRow
    events: list[EventRow] = Field(min_length=1, repr=False)

    @model_validator(mode="after")
    def validate_history(self) -> "Snapshot":
        state = State.model_validate_json(self.state.payload)
        if state.pending is not None:
            raise ValueError("먼저 원본 pending 주문을 대조하세요")
        ids = [row.id for row in self.events]
        if ids != sorted(set(ids)) or ids[-1] > self.source_max_id:
            raise ValueError("이벤트 ID 순서 또는 범위가 다릅니다")
        if self.source_next_id <= self.source_max_id:
            raise ValueError("원본 sequence가 최대 ID보다 커야 합니다")
        ledger = Ledger()
        last_state = None
        for row in self.events:
            if row.event not in KEEP_EVENTS:
                raise ValueError("승인된 핵심 이벤트만 가져올 수 있습니다")
            payload = json.loads(row.payload)
            if row.event == "initialization-approved":
                if payload != {}:
                    raise ValueError("초기화 승인 형식이 다릅니다")
            else:
                State.model_validate(payload["state"])
            if row.event in FINANCIAL_EVENTS:
                ledger.consume(row.event, row.payload, row.time)
                last_state = State.model_validate(payload["state"])
        if ledger.reason or ledger.capital is None or ledger.sequence != state.order_sequence:
            raise ValueError("손익 이력이 현재 상태와 일치하지 않습니다")
        if last_state is None or (last_state.krw, last_state.btc, last_state.order_sequence) != (
            state.krw,
            state.btc,
            state.order_sequence,
        ):
            raise ValueError("잔액 기준선이 현재 상태와 일치하지 않습니다")
        return self


def checksum(data: dict[str, object]) -> str:
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_snapshot(path: Path, expected_events: int) -> Snapshot:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"data", "sha256"} or checksum(envelope["data"]) != envelope["sha256"]:
        raise ValueError("스냅샷 체크섬이 일치하지 않습니다")
    snapshot = Snapshot.model_validate(envelope["data"])
    if len(snapshot.events) != expected_events:
        raise ValueError("예상 핵심 이벤트 행 수와 다릅니다")
    return snapshot


async def import_snapshot(engine: AsyncEngine, snapshot: Snapshot) -> None:
    async with locked_connection(engine) as connection:
        async with connection.begin():
            if await connection.run_sync(apply) != ("0001_postgresql",):
                raise ValueError("대상 revision이 다릅니다")
            for table in (states, events):
                if (await connection.execute(select(table.c.id).limit(1))).first() is not None:
                    raise ValueError("빈 대상 DB에만 가져올 수 있습니다")
            await connection.execute(insert(states), snapshot.state.model_dump())
            rows = [row.model_dump() for row in snapshot.events]
            await connection.execute(insert(events), rows)
            restored = (
                (await connection.execute(select(events).order_by(events.c.id))).mappings().all()
            )
            state = (await connection.execute(select(states))).mappings().one()
            if [dict(row) for row in restored] != rows or dict(
                state
            ) != snapshot.state.model_dump():
                raise ValueError("적재 후 원문 비교에 실패했습니다")
            # Transactional RESTART prevents partial sequence changes on a failed import.
            sequence = (
                await connection.execute(
                    text("SELECT pg_get_serial_sequence('evergreen_execution_event', 'id')")
                )
            ).scalar_one()
            schema, name = sequence.split(".", 1)
            quote = connection.dialect.identifier_preparer.quote
            await connection.execute(
                text(
                    f"ALTER SEQUENCE {quote(schema)}.{quote(name)} RESTART WITH {snapshot.source_next_id}"
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evergreen 2 핵심 상태를 PostgreSQL에 적재")
    parser.add_argument("input", type=Path)
    parser.add_argument("--expected-events", type=int, default=16)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        snapshot = read_snapshot(args.input, args.expected_events)
        if not args.check_only:
            load_spring_config(PlatformSettings())
            settings = DatabaseSettings()

            async def run() -> None:
                engine = create_async_engine(
                    settings.database_url,
                    connect_args=settings.connect_args,
                    poolclass=NullPool,
                    hide_parameters=True,
                )
                try:
                    await import_snapshot(engine, snapshot)
                finally:
                    await engine.dispose()

            asyncio.run(run())
        print(
            f"{'검증' if args.check_only else '적재'} 완료: 상태 1행, 이벤트 {len(snapshot.events)}행"
        )
        return 0
    except Exception as error:
        print(
            f"이관 중단 ({type(error).__name__}); 원문·연결 정보는 출력하지 않습니다.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
