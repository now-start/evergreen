"""Experiment 68: exhaustive single-account omission diagnostics, never a promotion gate."""

import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from statistics import median
from typing import Any

from evergreen.market import write_json
from evergreen.research.backtest import Result
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.strategy_family import summarize
from evergreen.research.report import source_identity

CADENCE = "liquidation-buffer-entry4h"
MODELS = ("breakout-v1", "entry-stop-budget", "liquidation-buffer", CADENCE)


def sensitivity(ids: list[str], candidate: list[Result], control: list[Result]) -> dict[str, Any]:
    if (
        len(ids) < 2
        or len(set(ids)) != len(ids)
        or len(candidate) != len(ids)
        or len(control) != len(ids)
    ):
        raise ValueError("계좌 식별자·개수·최소 표본이 잘못됐습니다")
    rows: list[dict[str, Any]] = []
    for i, (name, a, b) in enumerate(zip(ids, candidate, control, strict=True)):
        if (
            (a.start, a.end, a.initial_capital, a.costs, a.extra_delay_bars)
            != (b.start, b.end, b.initial_capital, b.costs, b.extra_delay_bars)
            or a.start >= a.end
            or (i and candidate[i - 1].end > a.start)
        ):
            raise ValueError("대응 계좌의 시간 순서·경계·비용 조건이 다릅니다")
        for item in (a, b):
            if (
                not item.net_return.is_finite()
                or item.net_return
                != (item.final_equity - item.initial_capital) / item.initial_capital
            ):
                raise ValueError("계좌 수익률이 평가금과 다릅니다")
        pnl, base_pnl = a.final_equity - a.initial_capital, b.final_equity - b.initial_capital
        rows.append(
            {
                "account": name,
                "start": a.start.isoformat(),
                "end": a.end.isoformat(),
                "hours": (a.end - a.start).total_seconds() / 3600,
                "candidate_return": str(a.net_return),
                "control_return": str(b.net_return),
                "return_difference": str(a.net_return - b.net_return),
                "candidate_pnl": str(pnl),
                "control_pnl": str(base_pnl),
                "pnl_difference": str(pnl - base_pnl),
                "candidate_trades": len(a.fills) // 2,
                "control_trades": len(b.fills) // 2,
                "candidate_max_drawdown": str(a.max_drawdown),
                "candidate_halted": a.halted,
            }
        )

    def metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "accounts": len(items),
            "paired_median_return": str(median(D(r["return_difference"]) for r in items)),
            "paired_total_pnl": str(sum((D(r["pnl_difference"]) for r in items), D(0))),
            "positive_accounts": sum(D(r["candidate_return"]) > 0 for r in items),
            "control_positive_accounts": sum(D(r["control_return"]) > 0 for r in items),
            "max_drawdown": str(max(D(r["candidate_max_drawdown"]) for r in items)),
            "halted_accounts": sum(r["candidate_halted"] for r in items),
        }

    full = metrics(rows)
    omitted = [
        {"excluded": name, **metrics(rows[:i] + rows[i + 1 :])} for i, name in enumerate(ids)
    ]

    def extremes(key: str) -> dict[str, Any]:
        values = [D(r[key]) for r in omitted]
        return {
            "min": str(min(values)),
            "max": str(max(values)),
            "min_excluded": [r["excluded"] for r in omitted if D(r[key]) == min(values)],
            "max_excluded": [r["excluded"] for r in omitted if D(r[key]) == max(values)],
        }

    def sign(value: str) -> str:
        return "positive" if D(value) > 0 else "negative" if D(value) < 0 else "zero"

    report: dict[str, Any] = {
        "rows": rows,
        "full": full,
        "leave_one_out": omitted,
        "median_range": extremes("paired_median_return"),
        "pnl_range": extremes("paired_total_pnl"),
    }
    for label, key in (("median", "paired_median_return"), ("pnl", "paired_total_pnl")):
        report[f"{label}_sign_counts"] = {
            s: sum(sign(r[key]) == s for r in omitted) for s in ("positive", "zero", "negative")
        }
        report[f"{label}_sign_changes"] = sum(sign(r[key]) != sign(full[key]) for r in omitted)
    return report


def run_study(reference: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "68",
            "reference_experiment": "67",
            "prespecified_in": "docs/breakout-entry-cadence-67.md",
            "models": MODELS,
            "scenarios": SCENARIOS,
            "trained_models": 0,
            "promotion_candidate": False,
            "live_enabled": False,
            "evaluation": "previously_observed_single_account_omission_sensitivity_not_inference",
            "omission_rule": "each_account_once_no_subset_selection_no_regating",
            **source_identity(),
        },
    )
    hashes: dict[str, str] = {}

    def read(path: Path, expected: dict[str, str] | None = None) -> Any:
        content = path.read_bytes()
        key, digest = str(path.resolve()), hashlib.sha256(content).hexdigest()
        if expected is not None and expected.get(key) != digest:
            raise ValueError(f"입력 해시 누락·불일치: {path.name}")
        hashes[key] = digest
        return json.loads(content)

    try:
        if (
            read(reference / "protocol.json")["experiment"] != "67"
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험67 결과가 필요합니다")
        expected = read(reference / "inputs.json")
        intervals = read(reference / "intervals.json")
        recorded = read(reference / "results.json")["scenarios"]
        directories = sorted((reference / "seed-17/continuous").glob("block-*"))
        if len(intervals) != len(directories) or len(intervals) < 2:
            raise ValueError("계좌 구간 누락·추가·표본 부족")
        ids = [p.name for p in directories]
        groups: dict[str, dict[str, list[Result]]] = {
            s: {m: [] for m in MODELS} for s, *_ in SCENARIOS
        }
        checked = 0
        for interval, directory in zip(intervals, directories, strict=True):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            for s, fee, slip, delay in SCENARIOS:
                for name in MODELS:
                    data = read(directory / f"{s}.{name}.json", expected)
                    result = Result.model_validate(data)
                    if (
                        result.start,
                        result.end,
                        result.costs,
                        result.extra_delay_bars,
                        result.initial_capital,
                    ) != (start, end, costs(fee, slip), delay, D(1000000)):
                        raise ValueError("원 실험 계좌 경계·원금·비용 조건이 다릅니다")
                    if result.strategy != (
                        "breakout-v1" if name == "breakout-v1" else "regime-mlp-v1"
                    ):
                        raise ValueError("원 실험 전략이 다릅니다")
                    trade_pnl(data)
                    if (
                        sum((f.fee for f in result.fills), D(0)) != result.total_fees
                        or sum((f.slippage_cost for f in result.fills), D(0))
                        != result.total_slippage
                        or result.final_equity != result.cash
                        or result.natural_exits
                        != sum(f.side == "sell" and f.reason != "settlement" for f in result.fills)
                    ):
                        raise ValueError("체결 비용·잔액·청산 횟수가 다릅니다")
                    groups[s][name].append(result)
                    checked += 1
        original = {
            s: {m: summarize(rows, g["breakout-v1"], s) for m, rows in g.items()}
            for s, g in groups.items()
        }
        for s, original_group in original.items():
            for name, row in original_group.items():
                if row != recorded[s][name]:
                    raise ValueError("원 실험 요약·게이트 재현 실패")
        comparisons = {}
        for s, group in groups.items():
            comparisons[s] = {
                f"{m}_vs_raw": sensitivity(ids, group[m], group["breakout-v1"]) for m in MODELS[1:]
            }
            comparisons[s]["buffer_vs_cadence"] = sensitivity(
                ids, group["liquidation-buffer"], group[CADENCE]
            )
        write_json(
            output / "results.json",
            {
                "original_summaries": original,
                "comparisons": comparisons,
                "promotion_candidate": False,
            },
        )
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_diagnostic",
                "checked_results": checked,
                "leave_one_out_comparisons": sum(
                    len(r["leave_one_out"]) for g in comparisons.values() for r in g.values()
                ),
                "promotion_candidate": False,
                "live_enabled": False,
            },
        )
        print(
            f"실험68 완료: 결과{checked}개 검증, {len(ids)}계좌 민감도 진단, 승격 없음", flush=True
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험68 계좌 민감도 진단 · 실계좌 접근 없음")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.reference, args.output)
