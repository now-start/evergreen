"""One-off export: run with the 2.2.7 environment, never with the v3 application."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.database.config import DatabaseSettings
from evergreen.platform.config import PlatformSettings, load_spring_config

KEEP = {
    "initialization-approved",
    "initialized",
    "cashflow-reconciled",
    "order-intent",
    "order-accepted",
    "order-terminal",
    "strategy-transition",
}
OMIT = {"valuation", "unexpected-balance", "strategy-evaluated", "no-signal"}
LOCK = "evergreen:execution:KRW-BTC"


async def export(expected_events: int) -> dict[str, object]:
    load_spring_config(PlatformSettings())
    engine = create_async_engine(
        DatabaseSettings().database_url, poolclass=NullPool, hide_parameters=True
    )
    if engine.dialect.name != "mysql":
        raise ValueError("2.2.7 원본 환경에서 실행하세요")
    try:
        async with engine.connect() as connection:
            locked = (
                await connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK})
            ).scalar_one()
            await connection.commit()
            if locked != 1:
                raise ValueError("원본 거래 워커를 먼저 중지하세요")
            try:
                await connection.execute(text("SET SESSION max_statement_time=30"))
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("START TRANSACTION WITH CONSISTENT SNAPSHOT"))
                revision = (
                    await connection.execute(
                        text("SELECT version_num FROM evergreen_alembic_version")
                    )
                ).scalar_one()
                if revision != "0002_remove_key_identity":
                    raise ValueError("원본 revision이 다릅니다")
                state = (
                    (
                        await connection.execute(
                            text("SELECT id,payload FROM evergreen_execution_state")
                        )
                    )
                    .mappings()
                    .one()
                )
                if state["id"] != 1 or json.loads(state["payload"]).get("pending") is not None:
                    raise ValueError("상태 또는 pending 주문을 확인하세요")
                kinds = set(
                    (
                        await connection.execute(
                            text("SELECT DISTINCT event FROM evergreen_execution_event")
                        )
                    ).scalars()
                )
                if kinds - KEEP - OMIT:
                    raise ValueError("미분류 이벤트가 있습니다")
                maximum = (
                    await connection.execute(text("SELECT MAX(id) FROM evergreen_execution_event"))
                ).scalar_one()
                next_id = (
                    await connection.execute(
                        text(
                            "SELECT AUTO_INCREMENT FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='evergreen_execution_event'"
                        )
                    )
                ).scalar_one()
                query = text(
                    "SELECT id,time,event,payload FROM evergreen_execution_event "
                    "WHERE event IN :kinds ORDER BY id"
                ).bindparams(bindparam("kinds", expanding=True))
                rows = (await connection.execute(query, {"kinds": sorted(KEEP)})).mappings().all()
                if len(rows) != expected_events:
                    raise ValueError("예상 핵심 이벤트 행 수와 다릅니다")
                data = {
                    "format": 1,
                    "source_revision": revision,
                    "source_max_id": maximum,
                    "source_next_id": max(next_id, maximum + 1),
                    "state": dict(state),
                    "events": [dict(row) for row in rows],
                }
                encoded = json.dumps(
                    data, sort_keys=True, ensure_ascii=True, separators=(",", ":")
                ).encode()
                return {"data": data, "sha256": hashlib.sha256(encoded).hexdigest()}
            finally:
                await connection.rollback()
                await connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK})
                await connection.commit()
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="중지한 Evergreen 2.2.7 원본에서 핵심 상태 추출")
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-events", type=int, default=16)
    args = parser.parse_args()
    try:
        data = asyncio.run(export(args.expected_events))
        # Never overwrite an earlier snapshot; financial contents stay in this private file.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=True)
        print(f"추출 완료: 상태 1행, 핵심 이벤트 {args.expected_events}행")
        return 0
    except Exception as error:
        print(f"추출 중단 ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
