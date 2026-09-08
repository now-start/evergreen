"""Migrate before telemetry, socket binding, health checks, or discovery."""

import asyncio
import logging

from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from evergreen.database.config import DatabaseSettings
from evergreen.database.migration import run_migrations
from evergreen.observability import operation
from evergreen.platform.config import PlatformSettings

logger = logging.getLogger(__name__)


def initialize_database(settings: PlatformSettings) -> None:
    if not settings.platform_integrations_enabled:
        logger.info("event=database_migration_skipped reason=local_profile")
        return
    try:
        with operation(logger, "database_migration", level=logging.INFO):
            asyncio.run(run_migrations(DatabaseSettings()))
    except (ValueError, RuntimeError, OSError, SQLAlchemyError, CommandError) as error:
        raise RuntimeError(
            f"DB 마이그레이션 실패 ({type(error).__name__}). 서비스 기동 중단."
        ) from None
