"""Experiment 56: offline, observational attribution of frozen account-limit breaches."""

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import FrameType
from typing import Any

from evergreen.market import load_dataset, write_json
from evergreen.research.backtest import Result, _Portfolio, run_backtest
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.report import source_identity

MODELS = ("entry-stop-budget", "entry-stop-trend-context", "extension-floor-2h")
LIMIT = Decimal(".1")


def observe(replay: Callable[[], Result]) -> tuple[Result, list[dict[str, Any]]]:
    """Read mark-return frames without changing prices, state, orders or source code.

    A close snapshot precedes that close's order decision; an open snapshot follows
    its due order execution. This is diagnostic-only, never a model feature input.
    """
    if sys.getprofile() is not None:
        raise ValueError("기존 프로파일러를 덮어쓰지 않습니다")
    marks: list[dict[str, Any]] = []

    def profile(frame: FrameType, event: str, arg: Any) -> None:
        if event != "return" or frame.f_code is not _Portfolio.mark.__code__:
            return
        parent = frame.f_back
        if parent is None or parent.f_code is not run_backtest.__code__:
            return
        values = frame.f_locals
        portfolio = values["self"]
        phase, time = values["phase"], values["time"]
        latest = portfolio.fills[-1] if portfolio.fills else None
        at_fill = (
            latest is not None
            and latest.time == time
            and (phase == "open" or (phase == "settlement" and latest.reason == "settlement"))
        )
        fill_cost = latest.fee + latest.slippage_cost if at_fill and latest else Decimal(0)
        point = portfolio.curve[-1]
        pending = parent.f_locals.get("pending")
        marks.append(
            {
                "time": time.isoformat(),
                "phase": phase,
                "equity": str(point.equity),
                "drawdown": str(point.drawdown),
                "peak": str(portfolio.peak),
                "cash": str(portfolio.cash),
                "btc": str(portfolio.btc),
                "reference_price": str(values["price"]),
                "fill_cost": str(fill_cost),
                "equity_before_fill_cost": str(point.equity + fill_cost),
                "fill": latest.model_dump(mode="json") if at_fill and latest else None,
                "latest_fill": latest.model_dump(mode="json") if latest else None,
                "pending": [*pending[:3], str(pending[3])] if pending else None,
                "pending_phase": "before_close_decision"
                if phase == "close"
                else "after_due_execution"
                if phase == "open"
                else "after_boundary_settlement",
            }
        )

    sys.setprofile(profile)
    try:
        result = replay()
    finally:
        sys.setprofile(None)
    return result, marks


def first_breach(result: Result, marks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(marks) != len(result.equity_curve) or any(
        Decimal(m["equity"]) != p.equity
        or Decimal(m["drawdown"]) != p.drawdown
        or datetime.fromisoformat(m["time"]) != p.time
        or m["phase"] != p.phase
        for m, p in zip(marks, result.equity_curve, strict=True)
    ):
        raise ValueError("관찰 자산 곡선 불일치")
    indices = [i for i, m in enumerate(marks) if Decimal(m["drawdown"]) >= LIMIT]
    if bool(indices) != result.halted:
        raise ValueError("중단 상태와 한도 초과가 다릅니다")
    if not indices:
        return None
    i = indices[0]
    mark = marks[i]
    cost_triggered = Decimal(mark["fill_cost"]) > 0 and Decimal(
        mark["equity_before_fill_cost"]
    ) > Decimal(mark["peak"]) * (1 - LIMIT)
    return {
        "first": mark,
        "previous": marks[i - 1] if i else None,
        "context": marks[max(0, i - 4) : i + 7],
        "classification": "fill_cost_crossing"
        if cost_triggered
        else "price_crossing_with_fill"
        if mark["fill"]
        else "holding_price_crossing"
        if Decimal(mark["btc"]) > 0
        else "cash_crossing",
        "max_drawdown": str(result.max_drawdown),
        "final_equity": str(result.final_equity),
    }


def analyze(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        content = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    write_json(
        output / "protocol.json",
        {
            "experiment": "56",
            "prespecified_in": "docs/breakout-extension-floor-55.md",
            "reference": str(source.resolve()),
            "models": MODELS,
            "live_enabled": False,
            "evaluation": "previously_observed_error_analysis_not_new_strategy",
            **source_identity(),
        },
    )
    try:
        if (
            read(source / "protocol.json")["experiment"] != "55"
            or read(source / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험55 결과가 필요합니다")
        intervals = read(source / "intervals.json")
        if not intervals:
            raise ValueError("평가 구간이 없습니다")
        datasets = []
        for directory in sorted((source / "datasets").glob("block-*")):
            for name in ("candles.jsonl", "quality.json"):
                path = directory / name
                hashes[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
            datasets.append(load_dataset(directory)[0])
        cases = []
        checked = 0
        comparisons = 0
        for i, interval in enumerate(intervals):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            matches = [
                b
                for b in datasets
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matches) != 1:
                raise ValueError("대조 계좌의 연속 원자료가 없습니다")
            bars = [b for b in matches[0] if start - timedelta(hours=200) <= b.open_time < end]
            directory = source / "seed-17/continuous" / f"block-{i:03d}"
            for scenario, fee, slip, delay in SCENARIOS:
                for model in ("breakout-v1", "cash", "prior", *MODELS):
                    payload = read(directory / f"{scenario}.{model}.json")
                    trade_pnl(payload)
                    checked += 1
                    if model not in MODELS:
                        continue
                    expected = Result.model_validate(payload)
                    if expected.start != start or expected.end != end:
                        raise ValueError("저장된 계좌 경계 불일치")
                    if expected.halted != (expected.max_drawdown >= LIMIT):
                        raise ValueError("저장된 중단 상태 불일치")
                    if not expected.halted:
                        continue

                    replay = partial(
                        run_backtest,
                        bars,
                        start,
                        Decimal(1000000),
                        costs(fee, slip),
                        "regime-mlp-v1",
                        extra_delay_bars=delay,
                        regimes={b.close_time: 2 for b in bars if b.close_time >= start},
                        regime_policy="breakout-filter",
                        breakout_entry_stop=True,
                        breakout_entry_stop_confirmations=2,
                        breakout_entry_stop_channel_reset=True,
                        breakout_entry_stop_profit_trail=True,
                        breakout_entry_stop_adaptive_trail=True,
                        breakout_entry_stop_trend_confirmation=True,
                        breakout_entry_stop_budget=True,
                        breakout_entry_stop_trend_lookback=168
                        if model == "entry-stop-trend-context"
                        else 24,
                        breakout_extension_floor=model == "extension-floor-2h",
                    )

                    actual, marks = observe(replay)
                    if actual.model_dump(mode="json") != payload:
                        raise ValueError("관찰 재생 전체 결과 불일치")
                    comparisons += 1
                    cases.append(
                        {
                            "block": i,
                            "scenario": scenario,
                            "model": model,
                            **(first_breach(actual, marks) or {}),
                        }
                    )
        write_json(output / "results.json", cases)
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed",
                "reconciled": checked,
                "replay_comparisons": comparisons,
                "breaches": len(cases),
                "live_enabled": False,
            },
        )
        print(
            f"실험56 완료: 손익 검산 {checked}개, 중단 경로 {comparisons}개 전체 재현", flush=True
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험56 위험 한도 초과 분석 · 실계좌 접근 없음")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.source, args.output)
