"""Explicit worker entrypoint, separate from FastAPI and experiment commands."""

import argparse
import asyncio
import logging
import sys
from time import monotonic

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from upbit import APIError

from evergreen.observability import configure_logging, operation
from evergreen.platform.config import PlatformSettings, load_spring_config
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader
from evergreen.trading.state import open_store
from evergreen.trading.upbit import Upbit

logger = logging.getLogger(__name__)


async def run(settings: TradingSettings, *, initialize: bool, once: bool) -> int:
    logger.info(
        "event=worker_started initialize=%s strategy=breakout-v1 market=KRW-BTC", initialize
    )
    engine = create_async_engine(settings.database_url, poolclass=NullPool, hide_parameters=True)
    try:
        async with open_store(engine, settings.identity, initialize=initialize) as store:
            if initialize:
                print("MariaDB 실행 상태 초기화 완료. 주문·계좌 조회 없음.")
                return 0
            api = Upbit(settings)
            try:
                trader = Trader(api, store, settings)
                heartbeat = monotonic()
                while True:
                    result = await trader.tick()
                    if monotonic() - heartbeat >= 60:
                        logger.info("event=worker_heartbeat result=%s", result)
                        heartbeat = monotonic()
                    if once:
                        return 0
                    await asyncio.sleep(settings.poll_seconds)
            finally:
                await api.close()
    finally:
        await engine.dispose()
        logger.info("event=worker_stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="업비트 단일 계정 주문 실행기 · 기본 비활성화")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--initialize-state",
        action="store_true",
        help="MariaDB 최초 실행 상태 생성(계좌 접근 없음)",
    )
    action.add_argument("--execute", action="store_true", help="Config Server 활성화 설정도 필요")
    parser.add_argument("--once", action="store_true", help="한 사이클만 실행")
    args = parser.parse_args(argv)
    configure_logging()
    if not args.initialize_state and not args.execute:
        print("비활성화: 주문 실행이나 계좌 조회를 하지 않았습니다.")
        return 0
    try:
        load_dotenv(override=False)
        configure_logging()
        with operation(logger, "worker_config_load", level=logging.INFO):
            load_spring_config(PlatformSettings())
        configure_logging()
        settings = TradingSettings()
        settings.require_credentials()
        if args.execute and not settings.live_enabled:
            raise ValueError("Config Server의 evergreen.execution.live-enabled가 false입니다")
        # No telemetry initialization: private request bodies/headers must not become spans.
        with operation(logger, "trading_worker", level=logging.INFO):
            return asyncio.run(run(settings, initialize=args.initialize_state, once=args.once))
    except KeyboardInterrupt:
        logger.info("event=worker_interrupted")
        return 0
    except (
        ValueError,
        OSError,
        RuntimeError,
        APIError,
        ArithmeticError,
        SQLAlchemyError,
    ) as error:
        logger.error(
            "event=worker_failed error_type=%s action=inspect_state_before_restart",
            type(error).__name__,
        )
        # Never print exception contents: validation/transport errors may embed private inputs.
        print(
            f"거래 중단 ({type(error).__name__}). DB 상태·주문을 확인한 뒤 재시작하세요.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
