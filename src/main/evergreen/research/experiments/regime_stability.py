"""Experiment 12: fixed-seed replication and a short, frozen-model September evaluation."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from evergreen.market import load_dataset, write_json
from evergreen.research.experiments.regime import SCENARIOS, Evaluation, evaluate, run_regime_study
from evergreen.research.experiments.regime_followup import evaluate_ablations
from evergreen.research.learning.models import window_features
from evergreen.research.learning.regime import (
    classification,
    load_regime,
    make_regime_samples,
    split_samples,
)
from evergreen.research.report import source_identity

SEEDS = (17, 29, 43)
RECENT_START = datetime(2026, 9, 1, tzinfo=UTC)
RECENT_END = datetime(2026, 9, 9, tzinfo=UTC)


def recent_evaluation(study: Path, dataset: Path, horizon: int = 24) -> None:
    bars, digest = load_dataset(dataset)
    if (
        bars[0].open_time != RECENT_START - timedelta(hours=169)
        or bars[-1].close_time != RECENT_END
    ):
        raise ValueError("최근 평가 구간·워밍업이 고정 계획과 다릅니다")
    model_path = study / "folds" / "2026-07-01" / "model"
    metadata = json.loads((model_path / "training.json").read_text())
    if metadata["horizon_hours"] != horizon:
        raise ValueError("고정 모델의 예측 기간이 다릅니다")
    model = load_regime(model_path, RECENT_START)
    predictions, probabilities = model.predict(bars)
    part = Evaluation(
        RECENT_START, bars, {t: v for t, v in predictions.items() if t >= RECENT_START}
    )
    output = study / "recent"
    evaluate(part, output)
    evaluate_ablations(part, output)
    samples = split_samples(make_regime_samples(bars, horizon), RECENT_START, RECENT_END)
    _, times = window_features(bars)
    write_json(
        output / "predictions.json",
        [
            {"time": t.isoformat(), "probabilities": p.tolist()}
            for t, p in zip(times, probabilities, strict=True)
            if t >= RECENT_START
        ],
    )
    write_json(
        output / "evaluation.json",
        {
            "dataset_sha256": digest,
            "model": str(model_path),
            "training": json.loads((model_path / "training.json").read_text()),
            "classification": classification(
                samples.labels, model.probabilities(samples.features), model.majority
            ),
            "independent_initial_capital": True,
            "live_enabled": False,
        },
    )


def candidate_rows(directory: Path) -> list[dict[str, Any]]:
    original = json.loads((directory / "results.json").read_text())
    rows = [
        {**r, "candidate": r["strategy"]}
        for r in original
        if r["strategy"] in ("cash", "breakout-v1")
    ] + [
        r
        for r in json.loads((directory / "ablations.json").read_text())
        if r["candidate"] == "breakout-filter"
    ]
    expected = {
        (s, c) for s, _, _, _ in SCENARIOS for c in ("cash", "breakout-v1", "breakout-filter")
    }
    if len(rows) != len(expected) or {(r["scenario"], r["candidate"]) for r in rows} != expected:
        raise ValueError("시드 비교의 후보·비용 결과가 누락되거나 중복됐습니다")
    if len({(r["start"], r["end"]) for r in rows}) != 1:
        raise ValueError("후보와 대조군의 평가 구간이 다릅니다")
    return rows


def summarize(studies: list[Path]) -> dict[str, object]:
    summaries: list[dict[str, object]] = []
    recent: list[dict[str, Any]] = []
    coverage = None
    reference = None
    stable = True
    for seed, study in zip(SEEDS, studies, strict=True):
        if json.loads((study / "protocol.json").read_text())["seed"] != seed:
            raise ValueError("시드 메타데이터 불일치")
        current = json.loads((study / "datasets" / "coverage.json").read_text())
        if coverage is not None and current != coverage:
            raise ValueError("시드별 원본 데이터가 다릅니다")
        coverage = current
        groups = [candidate_rows(p) for p in sorted((study / "continuous").glob("block-*"))]
        ranges = [(g[0]["start"], g[0]["end"]) for g in groups]
        if not groups or (reference is not None and ranges != reference):
            raise ValueError("시드별 평가 구간이 다릅니다")
        reference = ranges
        for scenario, _, _, _ in SCENARIOS:
            rows = [
                next(
                    r
                    for r in g
                    if r["scenario"] == scenario and r["candidate"] == "breakout-filter"
                )
                for g in groups
            ]
            controls = [
                next(r for r in g if r["scenario"] == scenario and r["candidate"] == "breakout-v1")
                for g in groups
            ]
            returns = [Decimal(r["net_return"]) for r in rows]
            differences = [
                r - Decimal(b["net_return"]) for r, b in zip(returns, controls, strict=True)
            ]
            exits = sum(r["natural_exits"] for r in rows)
            dd = max(Decimal(r["max_drawdown"]) for r in rows)
            positive = sum(r > 0 for r in returns)
            halted = sum(r["halted"] for r in rows)
            passes = (
                positive > len(rows) / 2
                and median(differences) > 0
                and dd <= Decimal(".10")
                and not halted
            )
            if scenario == "base":
                passes &= exits >= 10
            stable &= passes
            summaries.append(
                {
                    "seed": seed,
                    "scenario": scenario,
                    "blocks": len(rows),
                    "active_blocks": sum(Decimal(r["exposure_ratio"]) > 0 for r in rows),
                    "positive_blocks": positive,
                    "natural_exits": exits,
                    "median_return": str(median(returns)),
                    "worst_return": str(min(returns)),
                    "worst_drawdown": str(dd),
                    "halted_blocks": halted,
                    "median_paired_delta": str(median(differences)),
                    "outperformed_blocks": sum(d > 0 for d in differences),
                    "baseline_median_return": str(
                        median(Decimal(r["net_return"]) for r in controls)
                    ),
                    "exploratory_gate": bool(passes),
                }
            )
        if (study / "recent" / "evaluation.json").exists():
            recent.extend({"seed": seed, **r} for r in candidate_rows(study / "recent"))
    return {
        "historical": summaries,
        "recent": recent,
        "seed_stability": bool(stable),
        "live_promotion": False,
    }


def report(output: Path, results: dict[str, Any]) -> None:
    protocol = json.loads((output / "protocol.json").read_text())
    lines = [
        f"# 실험 {protocol['experiment']} — {protocol['horizon_hours']}시간 목표의 시드 안정성과 최근 8일 보조 평가",
        "",
        "과거 구간은 이미 관찰한 탐색 자료입니다. 세 시드는 독립 시장 표본이 아닙니다.",
        "원금은 연속 블록마다 독립 100만 원. 수익 중앙값은 누적 수익이 아니며, 미거래 블록도 포함합니다.",
        "돌파 대비 차이는 동일 블록 수익률 차이의 중앙값입니다. 시드별 최고 결과를 선택하지 않습니다.",
        "",
        "| 시드 | 비용 | 수익 중앙값 | 최악 낙폭 | 거래 구간/전체 | 양수 구간 | 청산 | 돌파 대비 차이 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results["historical"]:
        lines.append(
            f"| {r['seed']} | {r['scenario']} | {Decimal(r['median_return']):.2%} | {Decimal(r['worst_drawdown']):.2%} | {r['active_blocks']}/{r['blocks']} | {r['positive_blocks']} | {r['natural_exits']} | {Decimal(r['median_paired_delta']):.2%}p |"
        )
    lines += [
        "",
        "## 최근 보조 평가",
        "",
        "UTC 2026-09-01~09-09, 독립 원금으로 재시작. 7월 분기 모델 고정, 재학습 없음.",
        "192시간의 사후 평가이며 실시간 paper 결과가 아닙니다. 8일만으로 수익성을 입증하지 않습니다.",
        "",
        "| 시드 | 후보 | 비용 | 순수익률 | 낙폭 | 청산 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for r in results["recent"]:
        lines.append(
            f"| {r['seed']} | {r['candidate']} | {r['scenario']} | {Decimal(r['net_return']):.2%} | {Decimal(r['max_drawdown']):.2%} | {r['natural_exits']} |"
        )
    lines += [
        "",
        f"시드 안정성 사전 조건 충족: {results['seed_stability']}. 실거래 승격: 없음.",
        "최근 데이터 품질 실패가 있다면 각 시드 recent-failure.json에 기록됩니다. 무거래는 수익성 성공이 아닙니다.",
        "",
    ]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


def run_stability(source: Path, recent: Path, output: Path, *, horizon: int = 24) -> None:
    if horizon not in (24, 72):
        raise ValueError("장세 예측 기간은 24 또는 72시간이어야 합니다")
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "12" if horizon == 24 else "13",
            "horizon_hours": horizon,
            "seeds": SEEDS,
            "balanced": True,
            "candidate": "breakout-filter",
            "recent_start": RECENT_START.isoformat(),
            "recent_end": RECENT_END.isoformat(),
            "gate": "all seeds/costs: >50% positive blocks, paired median delta>0, DD<=10%, no halt; base exits>=10",
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        studies = []
        for seed in SEEDS:
            study = output / f"seed-{seed}"
            print(f"시드 안정성 학습 및 평가: {seed}", flush=True)
            run_regime_study(
                source, study, balanced=True, followups=True, seed=seed, horizon=horizon
            )
            # Missing recent candles must not erase completed historical replication.
            try:
                recent_evaluation(study, recent, horizon)
            except (ValueError, OSError) as error:
                write_json(
                    study / "recent-failure.json", {"error": str(error), "live_enabled": False}
                )
            studies.append(study)
        results = summarize(studies)
        write_json(output / "results.json", results)
        report(output, results)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "seed_stability": results["seed_stability"],
                "recent_complete": all(
                    (s / "recent" / "evaluation.json").exists() for s in studies
                ),
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError, KeyError, TypeError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험 12: 시드 안정성 및 최근 구간 평가")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--recent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizon", type=int, choices=(24, 72), default=24)
    args = parser.parse_args()
    run_stability(args.source, args.recent, args.output, horizon=args.horizon)
