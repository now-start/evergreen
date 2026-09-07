"""Experiment 04: controlled fixed-budget versus early-stopping comparison."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import torch

from evergreen.market import write_json
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments.deep import (
    BASELINES,
    SELECTION,
    TESTS,
    TRAINING,
    _date,
    _prediction_artifacts,
    _read,
)
from evergreen.research.experiments.rules import _four
from evergreen.research.learning.models import (
    Samples,
    TrainedModel,
    load_model,
    make_samples,
    train_model,
)
from evergreen.research.report import STRATEGY_LABELS, compare, source_identity
from evergreen.strategies import ML_STRATEGIES, Strategy

BOUNDARY = datetime(2024, 12, 1, tzinfo=UTC)
EVALUATIONS = (*SELECTION, TESTS[0])


def split_samples(
    segments: list[Samples], boundary: datetime
) -> tuple[list[Samples], list[Samples]]:
    if boundary.tzinfo is None:
        raise ValueError("분리 경계에 시간대가 필요합니다")
    training: list[Samples] = []
    validation: list[Samples] = []
    for sample in segments:
        for destination, times, before in (
            (training, sample.label_times, True),
            (validation, sample.signal_times, False),
        ):
            keep = [time < boundary if before else time >= boundary for time in times]
            if not any(keep):
                continue
            mask = torch.tensor(keep, dtype=torch.bool)
            destination.append(
                Samples(
                    sample.features[mask],
                    sample.labels[mask],
                    tuple(
                        time
                        for time, selected in zip(sample.signal_times, keep, strict=True)
                        if selected
                    ),
                    tuple(
                        time
                        for time, selected in zip(sample.label_times, keep, strict=True)
                        if selected
                    ),
                )
            )
    if not training or not validation:
        raise ValueError("학습과 검증 표본을 모두 분리할 수 있어야 합니다")
    return training, validation


def run_early_study(training_paths: list[Path], evaluation_paths: list[Path], output: Path) -> None:
    if len(training_paths) != 3 or len(evaluation_paths) != 4:
        raise ValueError("학습 데이터 3개와 재비교 데이터 4개가 필요합니다")
    output.mkdir(parents=True, exist_ok=False)
    completed: dict[str, dict[str, list[Result]]] = {"fixed20": {}, "early": {}}
    models: dict[str, dict[Strategy, TrainedModel]] = {"fixed20": {}, "early": {}}
    costs = Costs.model_validate(
        {
            "buy_fee": "0.0005",
            "sell_fee": "0.0005",
            "buy_slippage": "0.001",
            "sell_slippage": "0.001",
            "min_notional": "5000",
            "quantity_step": "0.00000001",
        }
    )
    try:
        write_json(
            output / "protocol.json",
            {
                "experiment": "04",
                "training": TRAINING,
                "validation_boundary": BOUNDARY.isoformat(),
                "evaluations": EVALUATIONS,
                "evaluation_status": "previously_observed_recomparison",
                "fixed_epochs": 20,
                "max_epochs": 100,
                "patience": 10,
                "min_delta": 0,
                "costs": costs.model_dump(mode="json"),
                "capital": "1000000",
                "created_at": datetime.now(UTC).isoformat(),
                **source_identity(),
            },
        )
        loaded = [
            _read(path, interval) for path, interval in zip(training_paths, TRAINING, strict=True)
        ]
        original = [make_samples(candles) for candles, _ in loaded]
        training, validation = split_samples(original, BOUNDARY)
        train_count = sum(len(sample.labels) for sample in training)
        val_count = sum(len(sample.labels) for sample in validation)
        write_json(
            output / "split.json",
            {
                "datasets": [
                    {"path": str(path.resolve()), "sha256": digest}
                    for path, (_, digest) in zip(training_paths, loaded, strict=True)
                ],
                "training_samples": train_count,
                "validation_samples": val_count,
                "purged_samples": sum(len(sample.labels) for sample in original)
                - train_count
                - val_count,
                "last_training_label": max(t for s in training for t in s.label_times).isoformat(),
                "first_validation_signal": min(
                    t for s in validation for t in s.signal_times
                ).isoformat(),
            },
        )
        for mode in models:
            for architecture in ML_STRATEGIES:
                directory = output / "models" / mode / architecture
                fitted = train_model(
                    training,
                    architecture,
                    directory,
                    epochs=20 if mode == "fixed20" else 100,
                    validation=validation,
                    patience=None if mode == "fixed20" else 10,
                )
                restored = load_model(directory)
                if not torch.equal(
                    fitted.probabilities(validation[0].features),
                    restored.probabilities(validation[0].features),
                ):
                    raise ValueError("최적 모델 재로딩 시 예측이 달라졌습니다")
                models[mode][architecture] = restored
        # All training choices are frozen before reading downstream comparison data.
        write_json(
            output / "training-complete.json",
            {
                mode: {name: model.metadata.checkpoint_sha256 for name, model in group.items()}
                for mode, group in models.items()
            },
        )
        for index, (path, interval) in enumerate(
            zip(evaluation_paths, EVALUATIONS, strict=True), 1
        ):
            name = f"comparison-{index}"
            candles, _ = _read(path, interval)
            start = _date(interval[1])
            for mode, group in models.items():
                scores = _prediction_artifacts(
                    group, candles, start, output / "predictions" / mode / name
                )
                completed[mode][name] = compare(
                    path,
                    start,
                    Decimal(1000000),
                    costs,
                    output / mode / name,
                    strategies=(*BASELINES, *ML_STRATEGIES),
                    predictions=scores,
                )
        rows = [
            "# 실험 04 — 조기 종료와 20 epoch 고정 비교",
            "",
            "**이미 관찰한 구간의 재비교이며 수익성 최종 검증이나 실거래 승격이 아닙니다.**",
            "",
            "같은 축소 학습 자료·시드·구조·비용으로 비교했습니다. 조기 종료 판단은 2024년 12월 검증 BCE만 사용합니다.",
            f"학습 표본 {train_count:,}개 / 검증 표본 {val_count:,}개 / 최대 100 epoch·patience 10.",
            "",
            "## 학습 결과",
            "",
            "| 모델 | 방식 | 실제 epoch | 최적 epoch | 저장 epoch | 저장 모델 검증 BCE |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for architecture in ML_STRATEGIES:
            for mode, group in models.items():
                meta = group[architecture].metadata
                saved = meta.checkpoint_epoch or meta.epochs
                rows.append(
                    f"| {STRATEGY_LABELS[architecture]} | {'고정20' if mode == 'fixed20' else '조기 종료'} | "
                    f"{meta.epochs} | {meta.best_epoch} | {saved} | {meta.validation_losses[saved - 1]:.4f} |"
                )
        rows.extend(
            [
                "",
                "## 동일 조건의 순수익률 비교",
                "",
                "| 구간 | 모델 | 고정20 기본 | 조기 종료 기본 | 차이(%p) | 고정20 최악 낙폭 | 조기 종료 최악 낙폭 |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for name in completed["fixed20"]:
            for architecture in ML_STRATEGIES:
                fixed = _four(completed["fixed20"][name], architecture)
                early = _four(completed["early"][name], architecture)
                rows.append(
                    f"| {name} | {STRATEGY_LABELS[architecture]} | {fixed[0].net_return:.2%} | "
                    f"{early[0].net_return:.2%} | {(early[0].net_return - fixed[0].net_return) * 100:+.2f} | "
                    f"{max(r.max_drawdown for r in fixed):.2%} | {max(r.max_drawdown for r in early):.2%} |"
                )
        rows.extend(
            [
                "",
                "## 해석과 상세 결과",
                "",
                "낙폭은 네 비용·지연 조건 중 최악의 값이며 시가·종가 관측에 한정됩니다.",
                "검증 손실 감소는 거래 수익 증가를 보장하지 않습니다. 0%는 매매하지 않은 결과일 수 있습니다.",
                "이전 실험 03의 20 epoch 모델은 학습 자료가 더 많아 직접적인 대조군으로 사용하지 않았습니다.",
                "모의 원금은 구간별 100만 원, 편도 수수료 0.05%·슬리피지 0.1%입니다. 결과를 연간 수익으로 합산하지 않습니다.",
                "2026년 평가 B는 데이터 누락 상태로 제외되어 있습니다. 새로운 미관측 최종 검증은 별도로 필요합니다.",
                "",
            ]
        )
        for index, interval in enumerate(EVALUATIONS, 1):
            rows.append(f"- 구간 {index}: {interval[1]} ~ {interval[2]} UTC, 종료 제외")
            for mode in models:
                rows.append(f"  - [{mode} 상세 결과]({mode}/comparison-{index}/summary.md)")
        with (output / "summary.md").open("x", encoding="utf-8") as stream:
            stream.write("\n".join(rows) + "\n")
        write_json(
            output / "status.json",
            {
                "comparison_completed": True,
                "live_enabled": False,
                "profitability_validated": False,
                "new_holdout_evaluated": False,
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError) as error:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "error": str(error),
                "completed": {mode: list(windows) for mode, windows in completed.items()},
            },
        )
        raise
