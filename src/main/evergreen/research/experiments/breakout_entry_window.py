"""Experiments 64-67: separately frozen entry-window, filter and cadence comparisons."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from evergreen.market import write_json
from evergreen.research.backtest import Result
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.regime_data import contiguous_blocks
from evergreen.research.experiments.strategy_family import CONTROLS, simulate, summarize
from evergreen.research.report import source_identity

CANDIDATE = "liquidation-buffer-entry84"
TREND_CANDIDATE = "liquidation-buffer-entry84-trend168"
SLOPE_CANDIDATE = "liquidation-buffer-entry84-slope168"
CADENCE_CANDIDATE = "liquidation-buffer-entry4h"
SHORT_MODELS = (CANDIDATE, TREND_CANDIDATE, SLOPE_CANDIDATE)
MODELS = (*CONTROLS, CANDIDATE)


def run_study(
    raw: Path,
    reference: Path,
    output: Path,
    *,
    trend_filter: bool = False,
    slope_filter: bool = False,
    entry_cadence: bool = False,
) -> None:
    if sum((trend_filter, slope_filter, entry_cadence)) > 1:
        raise ValueError("가격·방향·간격 실험을 결합하지 않습니다")
    experiment = "66" if slope_filter else "65" if trend_filter else "64"
    reference_experiment = "65" if slope_filter else "64" if trend_filter else "62"
    candidate = SLOPE_CANDIDATE if slope_filter else TREND_CANDIDATE if trend_filter else CANDIDATE
    controls = (*MODELS, TREND_CANDIDATE) if slope_filter else MODELS if trend_filter else CONTROLS
    if entry_cadence:
        experiment, reference_experiment = "67", "66"
        candidate, controls = CADENCE_CANDIDATE, CONTROLS
    models = (*controls, candidate)
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": experiment,
            "reference_experiment": reference_experiment,
            "candidate": candidate,
            "prespecified_in": "docs/breakout-entry-slope-filter-66.md"
            if entry_cadence
            else "docs/breakout-entry-trend-filter-65.md"
            if slope_filter
            else "docs/breakout-entry-window-64.md"
            if trend_filter
            else "docs/cost-path-63.md",
            "entry_trend_filter": "close_gt_prior168_mean_excluding_current"
            if trend_filter
            else None,
            "entry_slope_filter": "prior168_mean_gt_same_mean_24h_ago_excluding_current"
            if slope_filter
            else None,
            "entry_lookback": 168 if entry_cadence else 84,
            "entry_cadence_hours": 4 if entry_cadence else 1,
            "control_entry_cadence_hours": dict.fromkeys(controls, 1),
            "entry_clock": "completed_candle_close_utc",
            "exit_and_pending_order_cadence_hours": 1,
            "entry_multiplier": "1.001",
            "control_entry_lookbacks": {
                name: 84 if name in SHORT_MODELS else 168 for name in controls
            },
            "controls": controls,
            "warmup": 200,
            "capital": "1000000",
            "base_costs": costs().model_dump(mode="json"),
            "scenarios": SCENARIOS,
            "trained_models": 0,
            "live_enabled": False,
            "evaluation": "previously_observed_continuous_accounts_not_unseen_holdout",
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
            read(reference / "protocol.json")["experiment"] != reference_experiment
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError(f"완료된 실험{reference_experiment} 대조가 필요합니다")
        blocks = contiguous_blocks(raw, output / "datasets")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("원자료 해시·커버리지가 다릅니다")
        intervals = read(reference / "intervals.json")
        paths = sorted((reference / "seed-17/continuous").glob("*/base.breakout-v1.json"))
        if not intervals or len(intervals) != len(paths):
            raise ValueError("대조 계좌 구간이 누락됐습니다")
        write_json(output / "intervals.json", intervals)
        parts: dict[str, dict[str, list[Result]]] = {
            s: {m: [] for m in models} for s, *_ in SCENARIOS
        }
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
            for scenario, fee, slip, delay in SCENARIOS:
                for name in models:
                    result = simulate(
                        bars,
                        start,
                        "liquidation-buffer"
                        if name in (*SHORT_MODELS, CADENCE_CANDIDATE)
                        else name,
                        costs(fee, slip),
                        delay,
                        entry_lookback=84 if name in SHORT_MODELS else 168,
                        entry_trend_filter=name == TREND_CANDIDATE,
                        entry_slope_filter=name == SLOPE_CANDIDATE,
                        entry_cadence_hours=4 if name == CADENCE_CANDIDATE else 1,
                    )
                    if result.start != start or result.end != end:
                        raise ValueError("대조 계좌 경계가 다릅니다")
                    data = result.model_dump(mode="json")
                    trade_pnl(data)
                    pnl_checks += 1
                    filename = f"{scenario}.{name}.json"
                    write_json(directory / filename, data)
                    read(directory / filename)
                    if name in controls:
                        if data != read(path.parent / filename):
                            raise ValueError(f"대조 {name} 재현 실패")
                        comparisons += 1
                    parts[scenario][name].append(result)
            print(f"실험{experiment} {path.parent.name} 완료", flush=True)
        summaries = {
            s: {n: summarize(rows, group["breakout-v1"], s) for n, rows in group.items()}
            for s, group in parts.items()
        }
        buffer_comparison = {
            s: summarize(group[candidate], group["liquidation-buffer"], s)
            for s, group in parts.items()
        }
        # The shared summary names the paired field "vs_raw"; expose its actual comparator here.
        for row in buffer_comparison.values():
            row["paired_median_vs_buffer"] = row.pop("paired_median_vs_raw")
            row.pop("gate")
        window_comparison = {}
        if trend_filter or slope_filter:
            for scenario, group in parts.items():
                row = summarize(group[candidate], group[CANDIDATE], scenario)
                row["paired_median_vs_window"] = row.pop("paired_median_vs_raw")
                row.pop("gate")
                window_comparison[scenario] = row
        trend_comparison = {}
        if slope_filter:
            for scenario, group in parts.items():
                row = summarize(group[candidate], group[TREND_CANDIDATE], scenario)
                row["paired_median_vs_trend"] = row.pop("paired_median_vs_raw")
                row.pop("gate")
                trend_comparison[scenario] = row
        gate = all(group[candidate]["gate"] for group in summaries.values())
        write_json(
            output / "results.json",
            {
                "scenarios": summaries,
                "vs_buffer": buffer_comparison,
                "vs_window": window_comparison,
                "vs_trend": trend_comparison,
                "exploratory_gate": gate,
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
                "exploratory_gate": gate,
                "live_enabled": False,
            },
        )
        print(
            f"실험{experiment} 완료: 대조{comparisons}개, 손익{pnl_checks}개, 통과={gate}",
            flush=True,
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험64-67 돌파 진입 비교 · 실계좌 접근 없음")
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--trend-filter", action="store_true", help="실험65 이전168봉 평균가격 조건")
    modes.add_argument(
        "--slope-filter", action="store_true", help="실험66 이전168봉 평균 상승 방향"
    )
    modes.add_argument(
        "--entry-cadence", action="store_true", help="실험67 UTC 고정4시간 진입 평가"
    )
    args = parser.parse_args()
    run_study(
        args.raw,
        args.reference,
        args.output,
        trend_filter=args.trend_filter,
        slope_filter=args.slope_filter,
        entry_cadence=args.entry_cadence,
    )
