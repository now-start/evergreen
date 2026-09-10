"""Experiment 71: frozen entries, five prespecified offline exit policies."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from evergreen.market import Candle, load_dataset, write_json
from evergreen.research.backtest import Costs, ExitPolicy, Result, run_backtest
from evergreen.research.experiments.breakout_confirmation import wick_schedule
from evergreen.research.experiments.breakout_ensemble import mean_scores
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.strategy_family import summarize
from evergreen.research.report import source_identity

POLICIES = {
    "original": ExitPolicy(),
    "risk15": ExitPolicy(drawdown_limit=D(".15")),
    "risk20": ExitPolicy(drawdown_limit=D(".2")),
    "wide15": ExitPolicy(drawdown_limit=D(".15"), stop_multiple=D("4.5"), channel_hours=72),
    "wide20": ExitPolicy(drawdown_limit=D(".2"), stop_multiple=D(6), channel_hours=96),
}
RULES = ("breakout-v1", "wick-25", "entry-stop-trend-context", "liquidation-buffer")
MODELS = (*RULES, "ensemble-mlp")


class Inputs:
    def __init__(self) -> None:
        self.hashes: dict[str, str] = {}

    def read(self, path: Path, expected: dict[str, str] | None = None) -> Any:
        content = path.read_bytes()
        key, digest = str(path.resolve()), hashlib.sha256(content).hexdigest()
        if expected is not None and expected.get(key) != digest:
            raise ValueError(f"동결 입력 해시 불일치: {path.name}")
        self.hashes[key] = digest
        return json.loads(content)


def frozen_scores(root: Path, inputs: Inputs) -> dict[datetime, D]:
    source = root / "breakout-ensemble-23"
    declared = inputs.read(source / "inputs.json")
    protocol = inputs.read(source / "protocol.json")
    if protocol["members"] != [17, 29, 43] or protocol["experiment"] != "23":
        raise ValueError("MLP 앙상블 구성 변경")
    if inputs.read(source / "status.json")["status"] != "completed_partial_coverage":
        raise ValueError("미완료 앙상블")
    forecasts: dict[datetime, D] = {}
    paths = sorted((source / "candidate/seed-17/folds").glob("*/*.ensemble-mlp.json"))
    if not paths:
        raise ValueError("동결 MLP 예측이 없습니다")
    for path in paths:
        fold_start = datetime.fromisoformat(path.parent.name + "T00:00:00+00:00")
        members = []
        for seed in (17, 29, 43):
            fold = root / "breakout-overlap-22-candidate" / f"seed-{seed}/folds" / path.parent.name
            task = inputs.read(fold / "mlp-v1/task.json", declared)
            if datetime.fromisoformat(task["validation_label_max"]) >= fold_start:
                raise ValueError("모델에 미래 레이블이 포함됐습니다")
            members.append(
                {
                    datetime.fromisoformat(t): D(v)
                    for t, v in inputs.read(
                        fold / path.name.replace("ensemble-mlp", "mlp-v1"), declared
                    ).items()
                }
            )
        stored = {datetime.fromisoformat(t): D(v) for t, v in inputs.read(path).items()}
        if stored != mean_scores(members, set(stored)):
            raise ValueError("동결 3시드 평균과 저장 예측이 다릅니다")
        if any(t < fold_start for t in stored):
            raise ValueError("학습 분기 이전 예측")
        # Match joined_evaluations: the newer quarter owns its boundary prediction.
        for t, value in stored.items():
            if t in forecasts and t != fold_start:
                raise ValueError("분기 경계 외 중복 예측")
            forecasts[t] = value
    return forecasts


def simulate(
    bars: list[Candle],
    start: datetime,
    model: str,
    policy: ExitPolicy,
    pricing: Costs,
    delay: int,
    scores: dict[datetime, D],
) -> Result:
    if model not in MODELS:
        raise ValueError("지원하지 않는 고정 전략")
    required = {b.close_time for b in bars if b.close_time >= start}
    approvals = dict.fromkeys(required, 2)
    if model == "wick-25":
        approvals = wick_schedule(bars, start)
    elif model == "ensemble-mlp":
        if not required <= scores.keys():
            raise ValueError("평가 구간의 MLP 예측이 누락됐습니다")
        if any(not scores[t].is_finite() or not 0 <= scores[t] <= 1 for t in required):
            raise ValueError("MLP 점수가 유효하지 않습니다")
        approvals = {t: 2 if scores[t] > D(".5") else 0 for t in required}
    stops = model in ("entry-stop-trend-context", "liquidation-buffer")
    filtered = model != "breakout-v1"
    return run_backtest(
        bars,
        start,
        D(1000000),
        pricing,
        "regime-mlp-v1" if filtered else "breakout-v1",
        extra_delay_bars=delay,
        regimes=approvals if filtered else None,
        regime_policy="breakout-filter" if filtered else "routing",
        breakout_entry_stop=stops,
        breakout_entry_stop_confirmations=2 if stops else 1,
        breakout_entry_stop_channel_reset=stops,
        breakout_entry_stop_profit_trail=stops,
        breakout_entry_stop_adaptive_trail=stops,
        breakout_entry_stop_trend_confirmation=stops,
        breakout_entry_stop_budget=stops,
        breakout_entry_stop_trend_lookback=168 if model == "entry-stop-trend-context" else 24,
        breakout_liquidation_buffer=model == "liquidation-buffer",
        exit_policy=policy,
    )


def summarize_policy(
    rows: list[Result],
    raw: list[Result],
    original: list[Result],
    scenario: str,
    policy: ExitPolicy,
) -> dict[str, Any]:
    report = summarize(rows, raw, scenario)
    original_report = summarize(rows, original, scenario)
    report["old_10pct_gate"] = report.pop("gate")
    report["paired_median_vs_original"] = original_report["paired_median_vs_raw"]
    differences = [r.final_equity - o.final_equity for r, o in zip(rows, original, strict=True)]
    report["pnl_change_vs_original"] = str(sum(differences, D(0)))
    report["pnl_change_without_best_account"] = str(sum(differences, D(0)) - max(differences))
    report["improved_equal_worse_vs_original"] = [
        sum(x > 0 for x in differences),
        sum(x == 0 for x in differences),
        sum(x < 0 for x in differences),
    ]
    report["gate"] = (
        D(report["paired_median_vs_raw"]) > 0
        and D(report["paired_median_vs_original"]) > 0
        and report["positive_accounts"] > len(rows) / 2
        and D(report["max_drawdown"]) <= policy.drawdown_limit
        and report["halted_accounts"] == 0
        and (scenario != "base" or report["natural_exits"] >= 10)
    )
    return report


def run_study(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "71",
            "prespecified_in": "docs/relaxed-exits-71.md",
            "policies": {n: p.model_dump(mode="json") for n, p in POLICIES.items()},
            "models": MODELS,
            "scenarios": SCENARIOS,
            "capital": "1000000",
            "trained_models": 0,
            "mlp": "frozen_23_original_exit_labels",
            "historical_mlp": "not_applicable_no_future_training_backcast",
            "ranking": "minimum_total_pnl_across_four_scenarios_per_domain",
            "evaluation": "observed_historical_exploration_not_holdout",
            "live_enabled": False,
            **source_identity(),
        },
    )
    inputs = Inputs()
    try:
        scores = frozen_scores(root, inputs)
        references = {
            "breakout-v1": root / "breakout-entry-cadence-67/seed-17/continuous",
            "wick-25": root / "breakout-wick-28/seed-17/continuous",
            "entry-stop-trend-context": root
            / "breakout-entry-stop-trend-context-51/seed-17/continuous",
            "liquidation-buffer": root / "breakout-entry-cadence-67/seed-17/continuous",
            "ensemble-mlp": root / "breakout-ensemble-23/candidate/seed-17/continuous",
        }
        report: dict[str, Any] = {}
        comparisons = checks = 0
        for domain, reference, models in (
            ("recent", root / "breakout-entry-cadence-67", MODELS),
            ("historical", root / "historical-window-70", RULES),
        ):
            status = inputs.read(reference / "status.json")
            if not status["status"].startswith("completed"):
                raise ValueError("미완료 평가 대조")
            intervals = inputs.read(reference / "intervals.json")
            if not intervals:
                raise ValueError("평가 계좌 누락")
            blocks = []
            for dataset in sorted((reference / "datasets").glob("block-*")):
                bars, digest = load_dataset(dataset)
                inputs.hashes[str((dataset / "candles.jsonl").resolve())] = digest
                inputs.read(dataset / "quality.json")
                blocks.append(bars)
            groups: dict[str, dict[str, dict[str, list[Result]]]] = {
                p: {s: {m: [] for m in models} for s, *_ in SCENARIOS} for p in POLICIES
            }
            for index, interval in enumerate(intervals):
                start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
                account = interval.get("account", f"block-{index:03d}")
                matching = [
                    b
                    for b in blocks
                    if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
                ]
                if len(matching) != 1:
                    raise ValueError("계좌의 연속 자료가 없습니다")
                bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
                for policy_name, policy in POLICIES.items():
                    for scenario, fee, slip, delay in SCENARIOS:
                        for model in models:
                            r = simulate(
                                bars, start, model, policy, costs(fee, slip), delay, scores
                            )
                            if r.start != start or r.end != end:
                                raise ValueError("평가 경계 불일치")
                            data = r.model_dump(mode="json")
                            trade_pnl(data)
                            if policy_name == "original":
                                prior = (
                                    references[model] / account / f"{scenario}.{model}.json"
                                    if domain == "recent"
                                    else reference
                                    / "accounts"
                                    / account
                                    / f"{scenario}.{model}.json"
                                )
                                if domain == "recent" or model in (
                                    "breakout-v1",
                                    "liquidation-buffer",
                                ):
                                    if data != inputs.read(prior):
                                        raise ValueError(
                                            f"원래 전략 재현 실패: {domain}/{account}/{model}"
                                        )
                                    comparisons += 1
                            (output / domain / account).mkdir(parents=True, exist_ok=True)
                            write_json(
                                output
                                / domain
                                / account
                                / f"{policy_name}.{scenario}.{model}.json",
                                data,
                            )
                            groups[policy_name][scenario][model].append(r)
                            checks += 1
                print(f"71 {domain}/{account} 완료 ({checks} results)", flush=True)
            summaries = {
                p: {
                    s: {
                        m: summarize_policy(
                            rows,
                            groups[p][s]["breakout-v1"],
                            groups["original"][s][m],
                            s,
                            POLICIES[p],
                        )
                        for m, rows in models_rows.items()
                    }
                    for s, models_rows in conditions.items()
                }
                for p, conditions in groups.items()
            }
            ranking: list[dict[str, Any]] = [
                {
                    "policy": p,
                    "model": m,
                    "worst_condition_pnl": str(
                        min(D(summaries[p][s][m]["total_pnl"]) for s, *_ in SCENARIOS)
                    ),
                    "all_conditions_gate": all(summaries[p][s][m]["gate"] for s, *_ in SCENARIOS),
                }
                for p in POLICIES
                for m in models
            ]
            ranking.sort(key=lambda r: D(r["worst_condition_pnl"]), reverse=True)
            report[domain] = {
                "accounts": len(intervals),
                "intervals": intervals,
                "evaluated_hours": sum(
                    int(
                        (
                            datetime.fromisoformat(i["end"]) - datetime.fromisoformat(i["start"])
                        ).total_seconds()
                        / 3600
                    )
                    for i in intervals
                ),
                "summaries": summaries,
                "ranking": ranking,
            }
            write_json(output / domain / "results.json", report[domain])
        write_json(output / "results.json", report)
        write_json(output / "inputs.json", inputs.hashes)
        artifacts = {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*.json"))
        }
        write_json(output / "artifacts.json", artifacts)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "pnl_checks": checks,
                "original_json_matches": comparisons,
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, ArithmeticError, KeyError, TypeError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="기존/완화 청산 정책의 오프라인 전체 비교")
    parser.add_argument("--root", type=Path, default=Path("outputs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.root, args.output)


if __name__ == "__main__":
    main()
