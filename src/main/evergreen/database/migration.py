"""Packaged Alembic revisions and serialized PostgreSQL migration commands."""

import logging
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.database.config import DatabaseSettings
from evergreen.database.connection import locked_connection
from evergreen.observability import operation

logger = logging.getLogger(__name__)

Action = Literal["upgrade", "current"]
VERSION_TABLE = "evergreen_alembic_version"


def configuration() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    return config


def apply(connection: Connection, action: Action = "upgrade") -> tuple[str, ...]:
    config = configuration()
    config.attributes["connection"] = connection
    if connection.dialect.name != "postgresql":
        raise ValueError("PostgreSQL 연결이 필요합니다")
    if action == "upgrade":
        command.upgrade(config, "head")
    return MigrationContext.configure(
        connection, opts={"version_table": VERSION_TABLE}
    ).get_current_heads()


async def migrate(
    engine: AsyncEngine, action: Action = "upgrade", *, wait_seconds: int = 60
) -> tuple[str, ...]:
    async with locked_connection(engine, wait_seconds=wait_seconds) as connection:
        with operation(logger, "database_schema_apply", level=logging.INFO):
            heads = await connection.run_sync(apply, action)
            await connection.commit()
        logger.info(
            "event=database_revision action=%s revision=%s", action, ",".join(heads) or "base"
        )
        return heads


async def run_migrations(settings: DatabaseSettings, action: Action = "upgrade") -> tuple[str, ...]:
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args=settings.connect_args,
    )
    try:
        return await migrate(engine, action)
    finally:
        await engine.dispose()
