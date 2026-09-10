"""Experiment 72: one-factor ablations and early profit tracking for buffer 57."""

import argparse
import hashlib
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from statistics import median
from typing import Any

from evergreen.market import load_dataset, write_json
from evergreen.research.backtest import ExitPolicy, Result
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.relaxed_exits import Inputs, simulate, summarize_policy
from evergreen.research.report import source_identity

WIDE = ExitPolicy(drawdown_limit=D(".2"), stop_multiple=D(6), channel_hours=96)
POLICIES = {
    "wide20": WIDE,
    "channel48": ExitPolicy(drawdown_limit=D(".2"), stop_multiple=D(6)),
    "stop3": ExitPolicy(drawdown_limit=D(".2"), channel_hours=96),
    "risk15": ExitPolicy(drawdown_limit=D(".15"), stop_multiple=D(6), channel_hours=96),
    "early3": ExitPolicy(
        drawdown_limit=D(".2"),
        stop_multiple=D(6),
        channel_hours=96,
        profit_activation_multiple=D(3),
    ),
    "original": ExitPolicy(),
    "raw-wide20": WIDE,
}


def entry_effects(candidate: Result, control: Result) -> dict[str, Any]:
    """Same signal comparison is observational, not forced-entry portfolio replay."""
    if (candidate.start, candidate.end, candidate.costs, candidate.extra_delay_bars) != (
        control.start,
        control.end,
        control.costs,
        control.extra_delay_bars,
    ):
        raise ValueError("매수 신호 비교의 계좌 조건 불일치")
    for result in (candidate, control):
        trade_pnl(result.model_dump(mode="json"))

    def trades(result: Result) -> dict[str, dict[str, Any]]:
        rows = {}
        for buy, sell in zip(result.fills[::2], result.fills[1::2], strict=True):
            if buy.signal_time is None:
                raise ValueError("매수 신호가 없습니다")
            net = (sell.price * sell.quantity - sell.fee) / (buy.price * buy.quantity + buy.fee) - 1
            rows[buy.signal_time.isoformat()] = {
                "entry_time": buy.time.isoformat(),
                "exit_time": sell.time.isoformat(),
                "net_return": str(net),
                "boundary_settlement": sell.reason == "settlement",
            }
        return rows

    new, old = trades(candidate), trades(control)
    common = sorted(new.keys() & old.keys())
    matched: list[dict[str, Any]] = [
        {
            "signal": k,
            "candidate": new[k],
            "control": old[k],
            "return_change": str(D(new[k]["net_return"]) - D(old[k]["net_return"])),
        }
        for k in common
    ]
    deltas = [D(r["return_change"]) for r in matched]
    return {
        "shared": len(common),
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "matched": matched,
        "matched_return_change_median": str(median(deltas)) if deltas else None,
        "matched_improved_equal_worse": [
            sum(x > 0 for x in deltas),
            sum(x == 0 for x in deltas),
            sum(x < 0 for x in deltas),
        ],
    }


def run_study(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "72",
            "prespecified_in": "docs/buffer-improvement-72.md",
            "policies": {n: p.model_dump(mode="json") for n, p in POLICIES.items()},
            "scenarios": SCENARIOS,
            "capital": "1000000",
            "trained_models": 0,
            "comparison": "same_signal_observation_and_full_account_replay",
            "evaluation": "observed_history_not_holdout",
            "live_enabled": False,
            **source_identity(),
        },
    )
    inputs = Inputs()
    reference = root / "relaxed-exits-71-verified"
    try:
        declared = {
            str((reference / k).resolve()): v
            for k, v in inputs.read(reference / "artifacts.json").items()
        }
        if inputs.read(reference / "protocol.json", declared)["experiment"] != "71":
            raise ValueError("실험71 대조가 필요합니다")
        if inputs.read(reference / "status.json")["status"] != "completed_partial_coverage":
            raise ValueError("미완료 대조")
        old = inputs.read(reference / "results.json", declared)
        old_inputs = inputs.read(reference / "inputs.json", declared)
        report: dict[str, Any] = {}
        checks = matches = 0
        for domain, dataset_name in (
            ("recent", "breakout-entry-cadence-67"),
            ("historical", "historical-window-70"),
        ):
            intervals = old[domain]["intervals"]
            if not intervals:
                raise ValueError("계좌 구간 누락")
            blocks = []
            for dataset in sorted((root / dataset_name / "datasets").glob("block-*")):
                inputs.read(dataset / "quality.json", old_inputs)
                bars, digest = load_dataset(dataset)
                key = str((dataset / "candles.jsonl").resolve())
                if old_inputs.get(key) != digest:
                    raise ValueError("대조 봉 해시 불일치")
                inputs.hashes[key] = digest
                blocks.append(bars)
            groups: dict[str, dict[str, list[Result]]] = {
                s: {p: [] for p in POLICIES} for s, *_ in SCENARIOS
            }
            effects: dict[str, dict[str, list[dict[str, Any]]]] = {
                s: {p: [] for p in POLICIES} for s, *_ in SCENARIOS
            }
            for i, interval in enumerate(intervals):
                start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
                account = interval.get("account", f"block-{i:03d}")
                matching = [
                    b
                    for b in blocks
                    if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
                ]
                if len(matching) != 1:
                    raise ValueError("계좌의 연속 자료가 없습니다")
                bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
                directory = output / domain / account
                directory.mkdir(parents=True)
                for scenario, fee, slip, delay in SCENARIOS:
                    baseline: Result | None = None
                    for name, policy in POLICIES.items():
                        model = "breakout-v1" if name == "raw-wide20" else "liquidation-buffer"
                        r = simulate(bars, start, model, policy, costs(fee, slip), delay, {})
                        if r.start != start or r.end != end:
                            raise ValueError("계좌 경계 불일치")
                        data = r.model_dump(mode="json")
                        trade_pnl(data)
                        if name in ("wide20", "original", "raw-wide20"):
                            old_policy = "original" if name == "original" else "wide20"
                            path = (
                                reference
                                / domain
                                / account
                                / f"{old_policy}.{scenario}.{model}.json"
                            )
                            if data != inputs.read(path, declared):
                                raise ValueError("기존 결과 재현 실패")
                            matches += 1
                        if name == "wide20":
                            baseline = r
                        if baseline is None:
                            raise ValueError("wide20 기준이 없습니다")
                        effects[scenario][name].append(
                            {"account": account, **entry_effects(r, baseline)}
                        )
                        write_json(directory / f"{scenario}.{name}.json", data)
                        groups[scenario][name].append(r)
                        checks += 1
                print(f"72 {domain}/{account} 완료 ({checks} results)", flush=True)
            summaries = {
                s: {
                    n: summarize_policy(rows, group["raw-wide20"], group["wide20"], s, POLICIES[n])
                    for n, rows in group.items()
                }
                for s, group in groups.items()
            }
            for summary in summaries.values():
                for row in summary.values():
                    for k in list(row):
                        if "original" in k:
                            row[k.replace("original", "wide20")] = row.pop(k)
            ranking: list[dict[str, Any]] = [
                {
                    "candidate": n,
                    "worst_condition_pnl": str(
                        min(D(summaries[s][n]["total_pnl"]) for s, *_ in SCENARIOS)
                    ),
                    "gate": all(summaries[s][n]["gate"] for s, *_ in SCENARIOS),
                }
                for n in POLICIES
            ]
            ranking.sort(key=lambda x: D(x["worst_condition_pnl"]), reverse=True)
            report[domain] = {
                "accounts": len(intervals),
                "intervals": intervals,
                "evaluated_hours": old[domain]["evaluated_hours"],
                "summaries": summaries,
                "ranking": ranking,
            }
            write_json(output / domain / "entry-effects.json", effects)
            write_json(output / domain / "results.json", report[domain])
        write_json(output / "results.json", report)
        write_json(output / "inputs.json", inputs.hashes)
        write_json(
            output / "artifacts.json",
            {
                str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(output.rglob("*.json"))
            },
        )
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "pnl_checks": checks,
                "original_json_matches": matches,
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, ArithmeticError, KeyError, TypeError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="57 완화형 분리 대조와 조기 추적 실험")
    parser.add_argument("--root", type=Path, default=Path("outputs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.root, args.output)


if __name__ == "__main__":
    main()
