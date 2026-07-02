"""여러 전략을 (선택적으로) 멀티코어 병렬로 평가하고 진행 상태를 보여준다.

백테스트는 CPU-bound(순수 파이썬 루프 + 지표 계산)라, 진짜 속도를 내려면 asyncio가 아니라
``ProcessPoolExecutor``(멀티코어)가 맞다. 노트북/macOS는 spawn 방식이라 워커 함수가
**최상위(module-level)** 여야 pickle·재임포트가 되므로 :func:`_run_job`을 여기에 둔다
(spawn 워커가 이 모듈을 재임포트하면 ``evergreen_lab`` __init__이 실행돼 전략이 자동 등록된다).

콜드 캐시에서 워커들이 같은 CSV를 동시에 fetch/write하면 중복 Upbit 호출·부분쓰기 레이스가
날 수 있으므로, 병렬 실행 전에 **부모 프로세스에서 interval별 캐시를 한 번만 워밍업**한다
(이후 워커는 읽기만 한다). 진행 바는 외부 의존성(tqdm) 없이 한 줄을 갱신하는 가벼운 구현이다.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime
from pathlib import Path

from evergreen_lab.api import evaluate
from evergreen_lab.data import load_candles
from evergreen_lab.engine import BacktestResult, Cost
from evergreen_lab.upbit_data import resolve_to_dt


def _run_job(
    name: str,
    interval: str,
    market: str,
    from_dt: datetime,
    to_dt: datetime | None,
    cost: Cost,
    cache_dir: str,
) -> tuple[str, BacktestResult]:
    """ProcessPoolExecutor(spawn) 워커: 반드시 최상위 함수여야 pickle/재임포트가 된다."""
    result = evaluate(
        name, market=market, from_dt=from_dt, to_dt=to_dt,
        interval=interval, cost=cost, cache_dir=cache_dir,
    )
    return name, result


def evaluate_strategies(
    jobs: Mapping[str, str] | Sequence[tuple[str, str]],
    *,
    market: str = "KRW-BTC",
    from_dt: datetime,
    to_dt: datetime | None = None,
    cost: Cost | None = None,
    cache_dir: str = "outputs/data/upbit-cache",
    parallel: bool = True,
    max_workers: int | None = None,
    progress: bool = True,
) -> dict[str, BacktestResult]:
    """``{name: interval}`` 또는 ``[(name, interval), ...]``를 평가해 ``{name: BacktestResult}`` 반환.

    반환 dict는 입력 순서를 보존한다. ``parallel=True``면 여러 전략을 멀티코어로 동시에 돌린다
    (프로세스 생성 오버헤드가 있어 전략이 많거나 무거운 워크로드일수록 이득이 크다);
    ``progress=True``면 완료 개수 진행 바를 출력한다.

    병렬 실행 전에 부모에서 ``to_dt``를 interval별로 확정하고 캐시를 워밍업하므로, 콜드 캐시라도
    워커는 읽기 전용이고 중복 fetch·부분쓰기 레이스·``now()`` 버킷 드리프트가 없다
    (첫 호출은 그만큼 네트워크 fetch 시간이 걸릴 수 있다).
    """
    pairs: list[tuple[str, str]] = (
        list(jobs.items()) if isinstance(jobs, Mapping) else [tuple(job) for job in jobs]
    )
    if not pairs:
        return {}
    cost = cost or Cost()
    use_parallel = parallel and len(pairs) > 1
    tick = _make_progress(len(pairs)) if progress else _noop
    collected: dict[str, BacktestResult] = {}

    if not use_parallel:
        for name, interval in pairs:
            _, collected[name] = _run_job(name, interval, market, from_dt, to_dt, cost, cache_dir)
            tick(name)
        return {name: collected[name] for name, _ in pairs}

    # CWD 의존성 제거: 모든 워커가 동일한 절대 캐시 경로를 쓰도록 부모에서 resolve.
    cache_dir = str(Path(cache_dir).resolve())
    # to_dt를 부모에서 interval별로 한 번 확정한다: 워커마다 now()를 따로 불러 캐시 버킷이 갈리는
    # 드리프트를 막고, 캐시도 부모에서만 채워(워커는 읽기 전용) write 레이스를 없앤다.
    resolved = {iv: resolve_to_dt(iv, to_dt) for iv in dict.fromkeys(interval for _, interval in pairs)}
    for interval, resolved_dt in resolved.items():
        load_candles(market=market, from_dt=from_dt, to_dt=resolved_dt, interval=interval, cache_dir=cache_dir)

    workers = max(1, min(max_workers or os.cpu_count() or 1, len(pairs)))
    pool = ProcessPoolExecutor(max_workers=workers)
    futures = [
        pool.submit(_run_job, name, interval, market, from_dt, resolved[interval], cost, cache_dir)
        for name, interval in pairs
    ]
    completed = 0
    try:
        for future in as_completed(futures):
            try:
                name, result = future.result()
            except BrokenProcessPool as exc:
                raise RuntimeError(
                    "병렬 실행이 중단됐습니다(BrokenProcessPool). parallel=False로 다시 시도하세요."
                ) from exc
            collected[name] = result
            completed += 1
            tick(name)
    finally:
        # 실패 시 대기 중인 작업을 취소해 다른 장시간 백테스트 종료를 기다리지 않는다.
        pool.shutdown(cancel_futures=True)
        if progress and completed < len(pairs):
            sys.stdout.write("\n")
            sys.stdout.flush()

    return {name: collected[name] for name, _ in pairs}


def _noop(_name: str) -> None:
    return None


def _make_progress(total: int) -> Callable[[str], None]:
    """의존성 없는 진행 바: 완료마다 한 줄을 덮어써서 갱신한다(잔여 문자 제거용 패딩 포함)."""
    width = 24
    state = {"done": 0, "maxlen": 0}

    def tick(name: str) -> None:
        state["done"] += 1
        done = state["done"]
        filled = int(width * done / total)
        bar = ("█" * filled) + ("·" * (width - filled))
        line = f"  평가 [{bar}] {done}/{total}  방금 완료: {name}"
        state["maxlen"] = max(state["maxlen"], len(line))  # 이전에 찍힌 더 긴 줄의 잔여 문자를 지운다
        end = "\n" if done >= total else ""
        sys.stdout.write("\r" + line.ljust(state["maxlen"]) + end)
        sys.stdout.flush()

    return tick
