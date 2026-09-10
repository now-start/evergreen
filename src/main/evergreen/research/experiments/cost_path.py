"""Experiment 63: diagnostic cost removal, not a tradable promotion candidate."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from statistics import median
from typing import Any

from evergreen.market import write_json
from evergreen.research.backtest import Result
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime_data import contiguous_blocks
from evergreen.research.experiments.strategy_family import simulate
from evergreen.research.report import source_identity

MODELS = ("rsi-rebound-v1", "band-reversion-v1", "breakout-v1", "liquidation-buffer")
SCENARIOS = (("base", 1, 1), ("zero-fee", 0, 1), ("zero-slip", 1, 0), ("zero-both", 0, 0))


def compare(base: Result, result: Result, fee: int, slip: int) -> dict[str, Any]:
    if fee not in (0, 1) or slip not in (0, 1):
        raise ValueError("비용 제거 조건이 아닙니다")
    if base.costs != costs() or result.costs != costs(fee, slip):
        raise ValueError("비용 조건이 다릅니다")
    if (base.start, base.end, base.initial_capital, base.strategy, base.extra_delay_bars) != (
        result.start,
        result.end,
        result.initial_capital,
        result.strategy,
        result.extra_delay_bars,
    ) or base.extra_delay_bars != 0:
        raise ValueError("대응 계좌 조건이 다릅니다")
    for item in (base, result):
        trade_pnl(item.model_dump(mode="json"))
        if (
            sum((f.fee for f in item.fills), D(0)) != item.total_fees
            or sum((f.slippage_cost for f in item.fills), D(0)) != item.total_slippage
        ):
            raise ValueError("체결 비용 합이 다릅니다")
    base_pnl = base.final_equity - base.initial_capital
    pnl = result.final_equity - result.initial_capital
    fixed = base_pnl + (1 - fee) * base.total_fees + (1 - slip) * base.total_slippage

    def schedule(item: Result) -> list[tuple[Any, ...]]:
        return [(f.side, f.signal_time, f.time, f.reason) for f in item.fills]

    return {
        "net_return": str(result.net_return),
        "pnl": str(pnl),
        "fixed_fill_pnl": str(fixed),
        "path_effect": str(pnl - fixed),
        "pnl_change_vs_base": str(pnl - base_pnl),
        "max_drawdown": str(result.max_drawdown),
        "halted": result.halted,
        "trades": len(result.fills) // 2,
        "natural_exits": result.natural_exits,
        "schedule_changed": schedule(base) != schedule(result),
        "total_fees": str(result.total_fees),
        "total_slippage": str(result.total_slippage),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("빈 계좌 비교입니다")
    totals = {
        key: str(sum((D(r[key]) for r in rows), D(0)))
        for key in (
            "pnl",
            "fixed_fill_pnl",
            "path_effect",
            "pnl_change_vs_base",
            "total_fees",
            "total_slippage",
        )
    }
    return {
        **totals,
        "accounts": len(rows),
        "median_return": str(median(D(r["net_return"]) for r in rows)),
        "positive_accounts": sum(D(r["net_return"]) > 0 for r in rows),
        "max_drawdown": str(max(D(r["max_drawdown"]) for r in rows)),
        "halted_accounts": sum(r["halted"] for r in rows),
        "trades": sum(r["trades"] for r in rows),
        "natural_exits": sum(r["natural_exits"] for r in rows),
        "changed_schedules": sum(r["schedule_changed"] for r in rows),
        "worse_than_base": sum(D(r["pnl_change_vs_base"]) < 0 for r in rows),
    }


def run_study(raw: Path, reference: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "63",
            "reference_experiment": "62",
            "prespecified_in": "docs/strategy-family-62.md",
            "models": MODELS,
            "scenarios": SCENARIOS,
            "extra_delay_bars": 0,
            "warmup": 200,
            "base_costs": costs().model_dump(mode="json"),
            "capital": "1000000",
            "trained_models": 0,
            "promotion_candidate": False,
            "live_enabled": False,
            "evaluation": "previously_observed_cost_removal_diagnostic_not_unseen_holdout",
            "fixed_fill_pnl": "base_pnl_plus_removed_base_fill_costs_no_resimulation",
            "path_effect": "rerun_pnl_minus_fixed_fill_pnl_including_sizing_and_risk_feedback",
            **source_identity(),
        },
    )
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        content = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    try:
        if (
            read(reference / "protocol.json")["experiment"] != "62"
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험62 대조가 필요합니다")
        blocks = contiguous_blocks(raw, output / "datasets")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("원자료 해시·커버리지가 다릅니다")
        intervals = read(reference / "intervals.json")
        paths = sorted((reference / "seed-17/continuous").glob("*/base.breakout-v1.json"))
        if not intervals or len(intervals) != len(paths):
            raise ValueError("대조 계좌 구간이 누락됐습니다")
        write_json(output / "intervals.json", intervals)
        groups: dict[str, dict[str, list[dict[str, Any]]]] = {
            scenario: {model: [] for model in MODELS} for scenario, _, _ in SCENARIOS
        }
        records = []
        comparisons = pnl_checks = 0
        for interval, path in zip(intervals, paths, strict=True):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            matching = [
                b
                for b in blocks
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matching) != 1:
                raise ValueError("계좌 구간의 연속 원자료가 없습니다")
            bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
            directory = output / "seed-17/continuous" / path.parent.name
            directory.mkdir(parents=True)
            for model in MODELS:
                base = simulate(bars, start, model, costs(), 0)
                if base.start != start or base.end != end:
                    raise ValueError("대조 계좌 경계가 다릅니다")
                if base.model_dump(mode="json") != read(path.parent / f"base.{model}.json"):
                    raise ValueError(f"대조 {model} 재현 실패")
                comparisons += 1
                for scenario, fee, slip in SCENARIOS:
                    result = (
                        base
                        if scenario == "base"
                        else simulate(bars, start, model, costs(fee, slip), 0)
                    )
                    row = compare(base, result, fee, slip)
                    pnl_checks += 1
                    artifact = directory / f"{scenario}.{model}.json"
                    write_json(artifact, result.model_dump(mode="json"))
                    read(artifact)
                    groups[scenario][model].append(row)
                    records.append(
                        {"account": path.parent.name, "model": model, "scenario": scenario, **row}
                    )
            print(f"실험63 {path.parent.name} 완료", flush=True)
        write_json(output / "records.json", records)
        write_json(
            output / "results.json",
            {
                "scenarios": {
                    s: {m: summarize(rows) for m, rows in group.items()}
                    for s, group in groups.items()
                },
                "promotion_candidate": False,
            },
        )
        for artifact in (output / "datasets").rglob("*.json*"):
            hashes[str(artifact.resolve())] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "control_comparisons": comparisons,
                "pnl_checks": pnl_checks,
                "promotion_candidate": False,
                "live_enabled": False,
            },
        )
        print(f"실험63 완료: 대조{comparisons}개, 손익{pnl_checks}개 검증", flush=True)
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험63 비용·계좌 경로 진단 · 실계좌 접근 없음")
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.raw, args.reference, args.output)
