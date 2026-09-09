"""Offline experiments 09-11: fixed routing ablations and training-only class weighting."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any

from evergreen.market import write_json
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.report import source_identity
from evergreen.strategies import Strategy
from evergreen.strategies.regime import RegimePolicy, stabilize

if TYPE_CHECKING:
    from evergreen.research.experiments.regime import Evaluation

CANDIDATES: tuple[tuple[str, Strategy, RegimePolicy], ...] = (
    ("band-only", "band-reversion-v1", "routing"),
    ("always-sideways", "regime-mlp-v1", "routing"),
    ("sideways-cash", "regime-mlp-v1", "sideways-cash"),
    ("breakout-filter", "regime-mlp-v1", "breakout-filter"),
)


def evaluate_ablations(part: "Evaluation", output: Path) -> None:
    from evergreen.research.experiments.regime import SCENARIOS

    required = {bar.close_time for bar in part.bars if bar.close_time >= part.start}
    schedule = stabilize(part.predictions)
    flat = stabilize({t: 1 for t in required})
    rows = []
    for scenario, fee, slip, delay in SCENARIOS:
        costs = Costs(
            buy_fee=Decimal(".0005") * fee,
            sell_fee=Decimal(".0005") * fee,
            buy_slippage=Decimal(".001") * slip,
            sell_slippage=Decimal(".001") * slip,
            min_notional=Decimal(5000),
            quantity_step=Decimal(".00000001"),
        )
        for candidate, strategy, policy in CANDIDATES:
            regimes = flat if candidate == "always-sideways" else schedule
            result = run_backtest(
                part.bars,
                part.start,
                Decimal(1000000),
                costs,
                strategy,
                extra_delay_bars=delay,
                regimes={t: regimes[t] for t in required} if candidate != "band-only" else None,
                regime_policy=policy,
            )
            identity = {"candidate": candidate, "regime_policy": policy, "scenario": scenario}
            write_json(
                output / f"{scenario}.{candidate}.json",
                {**result.model_dump(mode="json"), **identity},
            )
            rows.append(
                {
                    **result.model_dump(
                        mode="json", exclude={"fills", "equity_curve", "rejections"}
                    ),
                    **identity,
                }
            )
    write_json(output / "ablations.json", rows)


def aggregate(output: Path) -> list[dict[str, object]]:
    """Compare identical continuous blocks, retaining all costs and inactive candidates."""
    from evergreen.research.experiments.regime import SCENARIOS

    all_rows: list[dict[str, Any]] = []
    coverage = None
    reference_blocks = None
    for model in ("unweighted", "balanced"):
        study = output / model
        current = json.loads((study / "datasets" / "coverage.json").read_text())
        if coverage is not None and current != coverage:
            raise ValueError("두 학습 실험의 원본·평가 데이터가 다릅니다")
        coverage = current
        blocks = []
        for block in sorted((study / "continuous").glob("block-*")):
            original = json.loads((block / "results.json").read_text())
            rows = [
                {**row, "candidate": row["strategy"]}
                for row in original
                if row["strategy"] in ("cash", "breakout-v1", "regime-mlp-v1")
            ] + json.loads((block / "ablations.json").read_text())
            expected = {
                (candidate, scenario)
                for candidate in (
                    "cash",
                    "breakout-v1",
                    "regime-mlp-v1",
                    *(c[0] for c in CANDIDATES),
                )
                for scenario, _, _, _ in SCENARIOS
            }
            if (
                len(rows) != len(expected)
                or {(r["candidate"], r["scenario"]) for r in rows} != expected
            ):
                raise ValueError("정책·비용 비교 결과 누락 또는 중복")
            if any(
                (r["start"], r["end"]) != (original[0]["start"], original[0]["end"]) for r in rows
            ):
                raise ValueError("정책별 평가 구간이 다릅니다")
            blocks.append((original[0]["start"], original[0]["end"]))
            all_rows.extend({**row, "model": model, "block": block.name} for row in rows)
        if not blocks or (reference_blocks is not None and blocks != reference_blocks):
            raise ValueError("두 학습 실험의 연속 평가 구간이 다릅니다")
        reference_blocks = blocks
    summary = []
    for model, candidate, scenario in sorted(
        {(r["model"], r["candidate"], r["scenario"]) for r in all_rows}
    ):
        rows = [
            r
            for r in all_rows
            if (r["model"], r["candidate"], r["scenario"]) == (model, candidate, scenario)
        ]
        returns = [Decimal(r["net_return"]) for r in rows]
        summary.append(
            {
                "model": model,
                "candidate": candidate,
                "scenario": scenario,
                "blocks": len(rows),
                "median_return": str(median(returns)),
                "worst_return": str(min(returns)),
                "worst_drawdown": str(max(Decimal(r["max_drawdown"]) for r in rows)),
                "positive_blocks": sum(v > 0 for v in returns),
                "halted_blocks": sum(r["halted"] for r in rows),
                "natural_exits": sum(r["natural_exits"] for r in rows),
                "total_fees": str(sum(Decimal(r["total_fees"]) for r in rows)),
                "total_slippage": str(sum(Decimal(r["total_slippage"]) for r in rows)),
                "median_exposure": str(median(Decimal(r["exposure_ratio"]) for r in rows)),
            }
        )
    write_json(output / "comparisons.json", {"summary": summary, "blocks": all_rows})
    return summary


def report(output: Path, summary: list[dict[str, object]]) -> None:
    labels = {
        "cash": "현금",
        "breakout-v1": "기존 돌파",
        "regime-mlp-v1": "기존 장세 전환",
        "band-only": "항상 평균회귀",
        "always-sideways": "항상 보합 더미",
        "sideways-cash": "보합 신규 매수 금지",
        "breakout-filter": "상승 시 돌파 진입만 허용",
    }
    lines = [
        "# 실험 09-11 — 장세 분리 실험",
        "",
        "과거 관찰 데이터를 재사용한 탐색입니다. 실거래 변경·승격 없음.",
        "08b와 동일한 결측 제외·시간순 분할·비용·지연·위험 중단 조건입니다.",
        "각 연속 블록은 독립 원금 100만 원입니다. 중앙값은 전체 누적 수익률이 아닙니다.",
        "균형 학습은 학습 손실만 변경하고, 조기 종료는 두 모델 모두 비가중 검증 CE입니다.",
        "보합 신규 매수 금지는 기존 포지션의 돌파 매도/하락 청산을 유지합니다.",
        "진입 필터는 하락 장세여도 매도를 강제하지 않으며 기존 돌파 매도/위험 중단을 유지합니다.",
        "",
        "| 학습 | 정책 | 비용 | 수익 중앙값 | 최악 낙폭 | 양수 구간 | 청산 수 | 노출 중앙값 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {'균형' if row['model'] == 'balanced' else '기존'} | {labels[str(row['candidate'])]} | {row['scenario']} | "
            f"{Decimal(str(row['median_return'])):.2%} | {Decimal(str(row['worst_drawdown'])):.2%} | "
            f"{row['positive_blocks']}/{row['blocks']} | {row['natural_exits']} | {Decimal(str(row['median_exposure'])):.2%} |"
        )
    lines += ["", "## 분류 품질 (하락·보합·상승 순)", ""]
    for model in ("unweighted", "balanced"):
        results = json.loads((output / model / "results.json").read_text())
        folds = results["classification"]
        matrix = [
            [sum(f["confusion_matrix"][i][j] for f in folds) for j in range(3)] for i in range(3)
        ]
        precision = [matrix[i][i] / max(1, sum(r[i] for r in matrix)) for i in range(3)]
        recall = [matrix[i][i] / max(1, sum(matrix[i])) for i in range(3)]
        macro = (
            sum(
                2 * matrix[i][i] / max(1, sum(matrix[i]) + sum(r[i] for r in matrix))
                for i in range(3)
            )
            / 3
        )
        lines += [
            f"- {model}: 통합 macro F1={macro:.4f}, 정밀도={[round(v, 4) for v in precision]}, 재현율={[round(v, 4) for v in recall]}",
            f"  - 혼동 행렬(실제 행/예측 열): {matrix}",
        ]
    lines += [
        "",
        "확률 점수는 보정되지 않았습니다. 무거래 0%를 수익성 성공으로 판정하지 않습니다.",
        "단일 시드 결과이며 새로운 기간 검증이 필요합니다. 모든 비용/원금 곡선은 각 모델 continuous/에 보존됩니다.",
        "",
    ]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


def run_followup(source: Path, output: Path) -> None:
    from evergreen.research.experiments.regime import run_regime_study

    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiments": ["09", "10", "11"],
            "seed": 17,
            "models": ["unweighted", "balanced"],
            "weight_formula": "n_training / (3 * training_class_count)",
            "validation_objective": "unweighted_cross_entropy",
            "confidence": 0.55,
            "confirmation": 3,
            "cooldown_hours": 6,
            "candidate_policies": CANDIDATES,
            "selection": "exploratory comparison only; no live promotion; zero trades are not success",
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        for name in ("unweighted", "balanced"):
            print(f"실험 09-11: {name} 학습 및 고정 정책 비교", flush=True)
            run_regime_study(source, output / name, balanced=name == "balanced", followups=True)
        summary = aggregate(output)
        report(output, summary)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "live_enabled": False,
                "comparisons_sha256": hashlib.sha256(
                    (output / "comparisons.json").read_bytes()
                ).hexdigest(),
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험 09-11: 오프라인 장세 분리 실험")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_followup(args.source, args.output)
