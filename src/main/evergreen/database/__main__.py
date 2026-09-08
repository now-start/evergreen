"""Schema commands; credentials come from the existing Spring Config Server."""

import argparse
import asyncio
import sys
from typing import cast

from alembic import command
from alembic.util.exc import CommandError
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from evergreen.database.config import DatabaseSettings
from evergreen.database.migration import Action, configuration, run_migrations
from evergreen.observability import configure_logging
from evergreen.platform.config import PlatformSettings, load_spring_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evergreen MariaDB 스키마 마이그레이션")
    parser.add_argument("action", choices=["upgrade", "current", "baseline", "history"])
    args = parser.parse_args(argv)
    configure_logging()
    if args.action == "history":
        command.history(configuration())
        return 0
    try:
        load_dotenv(override=False)
        load_spring_config(PlatformSettings())
        configure_logging()
        heads = asyncio.run(run_migrations(DatabaseSettings(), cast(Action, args.action)))
        print("DB revision:", ", ".join(heads) or "base")
        return 0
    except (ValueError, RuntimeError, OSError, SQLAlchemyError, CommandError) as error:
        print(
            f"마이그레이션 중단 ({type(error).__name__}). DB와 마이그레이션 이력을 확인하세요.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
