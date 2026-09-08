"""Packaged Alembic revisions and serialized MariaDB migration commands."""

import logging
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.database.config import DatabaseSettings
from evergreen.database.connection import locked_connection
from evergreen.database.migrations.versions.v0001_execution import legacy_schema
from evergreen.observability import operation

logger = logging.getLogger(__name__)

Action = Literal["upgrade", "current", "baseline"]
VERSION_TABLE = "evergreen_alembic_version"


def configuration() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    return config


def apply(connection: Connection, action: Action = "upgrade") -> tuple[str, ...]:
    config = configuration()
    config.attributes["connection"] = connection
    context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
    if action == "baseline":
        if context.get_current_heads():
            raise ValueError("이미 마이그레이션 이력이 있습니다")
        schema = legacy_schema()
        inspector = inspect(connection)
        if not set(schema.tables) <= set(inspector.get_table_names()):
            raise ValueError("기존 실행 테이블 두 개가 모두 필요합니다")
        for name in schema.tables:
            primary_key = inspector.get_pk_constraint(name).get("constrained_columns")
            if primary_key != [column.name for column in schema.tables[name].primary_key]:
                raise ValueError("기존 실행 테이블의 기본 키가 초기 버전과 다릅니다")
            auto_column = schema.tables[name].autoincrement_column
            for column in inspector.get_columns(name):
                expected_auto = auto_column is not None and column["name"] == auto_column.name
                if bool(column.get("autoincrement", False)) != expected_auto:
                    raise ValueError("기존 실행 테이블의 AUTO_INCREMENT가 초기 버전과 다릅니다")
            options = inspector.get_table_options(name)
            if (
                str(options.get("mysql_engine", options.get("mariadb_engine", ""))).lower()
                != "innodb"
            ):
                raise ValueError("기존 실행 테이블은 InnoDB여야 합니다")
        comparison = MigrationContext.configure(
            connection,
            opts={
                "include_object": lambda obj, name, type_, reflected, compare_to: (
                    type_ != "table" or name in schema.tables
                ),
                "compare_type": True,
                "compare_server_default": True,
                "version_table": VERSION_TABLE,
            },
        )
        if compare_metadata(comparison, schema):
            raise ValueError("기존 실행 테이블 구조가 초기 버전과 다릅니다")
        command.stamp(config, "0001_execution")
    elif action == "upgrade":
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
    engine = create_async_engine(settings.database_url, poolclass=NullPool, hide_parameters=True)
    try:
        return await migrate(engine, action)
    finally:
        await engine.dispose()
