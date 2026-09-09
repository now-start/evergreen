"""Experiment 14: long-history features versus a matched short-feature control."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from evergreen.market import write_json
from evergreen.research.experiments.regime import SCENARIOS, run_regime_study
from evergreen.research.experiments.regime_stability import SEEDS, candidate_rows, summarize
from evergreen.research.learning.regime_features import FeatureSet
from evergreen.research.report import source_identity

MODES: tuple[FeatureSet, ...] = ("short-matched", "long")


def compare(output: Path) -> dict[str, Any]:
    summaries = {
        mode: summarize([output / mode / f"seed-{seed}" for seed in SEEDS]) for mode in MODES
    }
    paired = []
    for seed in SEEDS:
        left, right = [output / mode / f"seed-{seed}" for mode in MODES]
        if json.loads((left / "datasets/coverage.json").read_text()) != json.loads(
            (right / "datasets/coverage.json").read_text()
        ):
            raise ValueError("입력 비교의 원본 자료가 다릅니다")
        models = [sorted(p.glob("folds/*/model/training.json")) for p in (left, right)]
        if not models[0] or [p.parts[-3] for p in models[0]] != [p.parts[-3] for p in models[1]]:
            raise ValueError("입력 비교의 학습 분기가 다릅니다")
        for a, b in zip(*models, strict=True):
            first, second = json.loads(a.read_text()), json.loads(b.read_text())
            if first["feature_set"] != "short-matched" or second["feature_set"] != "long":
                raise ValueError("입력 종류가 비교 계획과 다릅니다")
            for key in (
                "training_sample_sha256",
                "validation_sample_sha256",
                "seed",
                "feature_channels",
                "feature_warmup",
                "horizon_hours",
                "max_epochs",
                "patience",
                "class_weights",
                "validation_objective",
            ):
                if first[key] != second[key]:
                    raise ValueError(f"입력 비교의 표본·학습 설정 불일치: {key}")
        groups = [
            [candidate_rows(p) for p in sorted(root.glob("continuous/block-*"))]
            for root in (left, right)
        ]
        if [(g[0]["start"], g[0]["end"]) for g in groups[0]] != [
            (g[0]["start"], g[0]["end"]) for g in groups[1]
        ]:
            raise ValueError("입력 비교의 평가 구간이 다릅니다")
        for scenario, _, _, _ in SCENARIOS:
            values = [
                [
                    next(
                        r
                        for r in group
                        if r["candidate"] == "breakout-filter" and r["scenario"] == scenario
                    )
                    for group in side
                ]
                for side in groups
            ]
            # Unmodified breakout must remain an identical price/cost control across both runs.
            for a, b in zip(*groups, strict=True):
                for candidate in ("cash", "breakout-v1"):
                    if next(
                        r for r in a if r["candidate"] == candidate and r["scenario"] == scenario
                    ) != next(
                        r for r in b if r["candidate"] == candidate and r["scenario"] == scenario
                    ):
                        raise ValueError("입력 비교의 공통 기준 전략 결과가 다릅니다")
            deltas = [
                Decimal(b["net_return"]) - Decimal(a["net_return"])
                for a, b in zip(*values, strict=True)
            ]
            paired.append(
                {
                    "seed": seed,
                    "scenario": scenario,
                    "blocks": len(deltas),
                    "long_minus_matched_median": str(median(deltas)),
                    "improved_blocks": sum(d > 0 for d in deltas),
                    "worse_blocks": sum(d < 0 for d in deltas),
                }
            )
    return {"variants": summaries, "paired": paired, "live_promotion": False}


def report(output: Path, results: dict[str, Any]) -> None:
    lines = [
        "# 실험 14 — 장기 추세 입력의 공정 비교",
        "",
        "같은 200시간 준비 구간·32x10 입력·학습 표본·시드·비용을 사용합니다.",
        "기존 입력 대조군은 추가 5채널을 0으로 채웠으며, 장기 입력군만 추세/변동성을 제공합니다.",
        "과거에 관찰한 탐색 자료입니다. 세 시드는 독립 시장 표본이 아니며 신규 홀드아웃 검증이 아닙니다.",
        "연속 블록마다 독립 원금 100만 원. 아래 중앙값은 누적 수익률이 아니며 미거래 구간을 포함합니다.",
        "기존 실험과 준비 구간이 다르므로 이번에 재실행한 동일 구간 돌파와 비교합니다.",
        "",
        "| 입력 | 시드 | 비용 | 수익 중앙값 | 최악 낙폭 | 거래 구간/전체 | 청산 | 돌파 대비 차이 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for mode, summary in results["variants"].items():
        for r in summary["historical"]:
            lines.append(
                f"| {mode} | {r['seed']} | {r['scenario']} | {Decimal(r['median_return']):.2%} | {Decimal(r['worst_drawdown']):.2%} | {r['active_blocks']}/{r['blocks']} | {r['natural_exits']} | {Decimal(r['median_paired_delta']):.2%}p |"
            )
    lines += [
        "",
        "## 장기 입력 - 기존 입력 대조군",
        "",
        "| 시드 | 비용 | 동일 블록 수익 차이 중앙값 | 개선/악화 블록 |",
        "|---|---|---:|---:|",
    ]
    for r in results["paired"]:
        lines.append(
            f"| {r['seed']} | {r['scenario']} | {Decimal(r['long_minus_matched_median']):.2%}p | {r['improved_blocks']}/{r['worse_blocks']} |"
        )
    lines += [
        "",
        "시드별 최고 결과를 선택하지 않습니다. 무거래는 수익성 입증이 아닙니다.",
        "200시간 준비 후 24시간 이상인 모든 연속 구간을 사용합니다. 결측 가격을 보간하지 않습니다.",
        "최근 8일은 이전 실험에서 이미 관찰했으므로 이번에는 신규 검증처럼 재평가하지 않았습니다.",
        "실거래 승격 없음. 모델 입력 외 전략·진입 확인·청산·위험 중단 정책은 변경하지 않았습니다.",
        "",
    ]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


def run_feature_study(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "14",
            "modes": MODES,
            "seeds": SEEDS,
            "horizon_hours": 24,
            "warmup_hours": 200,
            "input_shape": [32, 10],
            "balanced": True,
            "extra_features": [
                "log_return_24h",
                "log_return_72h",
                "log_return_168h",
                "log_sma48_over_sma168",
                "std_return_24h",
            ],
            "control": "short features plus five zero channels; same samples, initialization and architecture",
            "primary_candidate": "breakout-filter",
            "recent_evaluation": False,
            "adoption": "all seeds/costs pass existing stability gate and paired median long-minus-control > 0; still no automatic live promotion",
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        for mode in MODES:
            for seed in SEEDS:
                print(f"입력 비교: {mode}, 시드 {seed}", flush=True)
                run_regime_study(
                    source,
                    output / mode / f"seed-{seed}",
                    balanced=True,
                    followups=True,
                    seed=seed,
                    feature_set=mode,
                )
        results = compare(output)
        write_json(output / "results.json", results)
        report(output, results)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "live_enabled": False,
                "exploratory_gate": bool(
                    results["variants"]["long"]["seed_stability"]
                    and all(Decimal(r["long_minus_matched_median"]) > 0 for r in results["paired"])
                ),
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError, KeyError, TypeError) as error:
        write_json(
            output / "failure.json",
            {"status": "failed", "error": str(error), "live_enabled": False},
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="장기 추세 입력의 동일 표본·모델 크기 비교")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_feature_study(args.source, args.output)


if __name__ == "__main__":
    main()
