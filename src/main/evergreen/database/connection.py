"""One database-session lock shared by migrations and the execution worker."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

LOCK_NAME = "evergreen:execution:KRW-BTC"


class ExecutionLockError(RuntimeError):
    """The current session does not own the execution lock; reconnect before any order."""


@asynccontextmanager
async def locked_connection(
    engine: AsyncEngine, *, wait_seconds: int = 0
) -> AsyncIterator[AsyncConnection]:
    logger.info("event=database_lock_wait wait_seconds=%d", wait_seconds)
    async with engine.connect() as connection:
        locked = (
            await connection.execute(
                text("SELECT GET_LOCK(:name, :wait_seconds)"),
                {"name": LOCK_NAME, "wait_seconds": wait_seconds},
            )
        ).scalar_one()
        await connection.commit()
        if locked != 1:
            logger.error("event=database_lock_unavailable")
            raise ExecutionLockError(
                "다른 거래 실행기 또는 마이그레이션이 MariaDB 잠금을 보유하고 있습니다"
            )
        logger.info("event=database_lock_acquired")
        failed = False
        try:
            yield connection
        except BaseException:
            failed = True
            raise
        finally:
            try:
                if not connection.invalidated:
                    await connection.rollback()
                    await connection.execute(
                        text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME}
                    )
                    await connection.commit()
                    logger.info("event=database_lock_released")
            except Exception:
                if not failed:
                    raise
                logger.warning(
                    "event=database_lock_cleanup_failed action=preserve_original_failure"
                )
