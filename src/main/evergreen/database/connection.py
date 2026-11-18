"""One database-session lock shared by migrations and the execution worker."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

LOCK_NAMESPACE = 0x45564752  # Stable EVGR / KRW-BTC key, never Python's randomized hash().
LOCK_RESOURCE = 1
LOCK_PARAMS = {"namespace": LOCK_NAMESPACE, "resource": LOCK_RESOURCE}


class ExecutionLockError(RuntimeError):
    """The current session does not own the execution lock; reconnect before any order."""


async def assert_owner(connection: AsyncConnection, owner: int) -> None:
    if connection.invalidated:
        raise ExecutionLockError("PostgreSQL 실행 연결을 잃었습니다")
    owned = (
        await connection.execute(
            text(
                "SELECT pg_backend_pid() = :owner AND NOT pg_is_in_recovery() AND EXISTS ("
                "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = :owner "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
                "AND classid = :namespace AND objid = :resource AND objsubid = 2 "
                "AND mode = 'ExclusiveLock' AND granted)"
            ),
            {**LOCK_PARAMS, "owner": owner},
        )
    ).scalar_one()
    if not owned:
        logger.error("event=execution_lock_lost")
        raise ExecutionLockError("PostgreSQL 실행 잠금을 잃었습니다")


@asynccontextmanager
async def locked_connection(
    engine: AsyncEngine, *, wait_seconds: int = 0
) -> AsyncIterator[AsyncConnection]:
    logger.info("event=database_lock_wait wait_seconds=%d", wait_seconds)
    deadline = monotonic() + wait_seconds
    async with engine.connect() as connection:
        failed = False
        acquired = False
        try:
            while True:
                acquired = bool(
                    (
                        await connection.execute(
                            text("SELECT pg_try_advisory_lock(:namespace, :resource)"), LOCK_PARAMS
                        )
                    ).scalar_one()
                )
                await connection.commit()
                if acquired:
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    logger.error("event=database_lock_unavailable")
                    raise ExecutionLockError(
                        "다른 거래 실행기 또는 마이그레이션이 잠금을 보유하고 있습니다"
                    )
                await asyncio.sleep(min(0.1, remaining))
            logger.info("event=database_lock_acquired")
            yield connection
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if acquired and not connection.invalidated:
                    await connection.rollback()
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:namespace, :resource)"), LOCK_PARAMS
                    )
                    await connection.commit()
                    logger.info("event=database_lock_released")
            except Exception:
                await connection.invalidate()
                if not failed:
                    raise
                logger.warning(
                    "event=database_lock_cleanup_failed action=preserve_original_failure"
                )
