"""Explicit worker entrypoint, separate from FastAPI and experiment commands."""

import argparse
import asyncio
import sys

import ccxt
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from evergreen.platform.config import PlatformSettings, load_spring_config
from evergreen.trading.config import TradingSettings
from evergreen.trading.engine import Trader
from evergreen.trading.state import open_store
from evergreen.trading.upbit import Upbit


async def run(settings: TradingSettings, *, initialize: bool, once: bool) -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with open_store(engine, settings.identity, initialize=initialize) as store:
            if initialize:
                print("MariaDB 실행 상태 초기화 완료. 주문·계좌 조회 없음.")
                return 0
            api = Upbit(settings)
            try:
                trader = Trader(api, store, settings)
                while True:
                    print(await trader.tick(), flush=True)
                    if once:
                        return 0
                    await asyncio.sleep(settings.poll_seconds)
            finally:
                await api.close()
    finally:
        await engine.dispose()


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
    if not args.initialize_state and not args.execute:
        print("비활성화: 주문 실행이나 계좌 조회를 하지 않았습니다.")
        return 0
    try:
        load_dotenv(override=False)
        load_spring_config(PlatformSettings())
        settings = TradingSettings()
        settings.require_credentials()
        if args.execute and not settings.live_enabled:
            raise ValueError("Config Server의 evergreen.execution.live-enabled가 false입니다")
        # No telemetry initialization: private request bodies/headers must not become spans.
        return asyncio.run(run(settings, initialize=args.initialize_state, once=args.once))
    except KeyboardInterrupt:
        return 0
    except (
        ValueError,
        OSError,
        RuntimeError,
        ccxt.BaseError,
        ArithmeticError,
        SQLAlchemyError,
    ) as error:
        # Never print exception contents: validation/transport errors may embed private inputs.
        print(
            f"거래 중단 ({type(error).__name__}). DB 상태·주문을 확인한 뒤 재시작하세요.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
