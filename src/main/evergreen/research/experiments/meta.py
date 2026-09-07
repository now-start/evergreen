"""Experiment 06: rule-conditioned net-profit labels and frozen meta-model selection."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import torch

from evergreen.market import write_json
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments.deep import (
    SELECTION,
    TESTS,
    TRAINING,
    _date,
    _prediction_artifacts,
    _read,
)
from evergreen.research.experiments.early_stopping import BOUNDARY, split_samples
from evergreen.research.experiments.hybrid import CONTROLS, candidate_score, frozen_models
from evergreen.research.experiments.rules import _four
from evergreen.research.learning.meta import event_samples, sample_counts
from evergreen.research.learning.models import TrainedModel, load_model, train_model
from evergreen.research.report import STRATEGY_LABELS, compare, source_identity
from evergreen.strategies import HYBRID_COMPONENTS, META_COMPONENTS, TIMED_RULES, Strategy

META_CONTROLS = (*CONTROLS, *HYBRID_COMPONENTS, *TIMED_RULES)


def training_eligible(training: dict[str, int], validation: dict[str, int]) -> bool:
    return (
        training["samples"] >= 50
        and validation["samples"] >= 20
        and min(
            training["positives"],
            training["negatives"],
            validation["positives"],
            validation["negatives"],
        )
        > 0
    )


def choose_meta(windows: dict[str, list[Result]], candidates: tuple[Strategy, ...]) -> Strategy:
    if set(windows) != {"selection-1", "selection-2", "selection-3"}:
        raise ValueError("메타 모델 선택에는 지정된 세 구간이 필요합니다")
    if any(name not in META_COMPONENTS for name in candidates):
        raise ValueError("지원하지 않는 메타 모델입니다")
    scores = {name: candidate_score(windows, name) for name in candidates}
    eligible = [name for name, score in scores.items() if score[0]]
    return (
        min(eligible, key=lambda name: (-scores[name][1], scores[name][2], name))
        if eligible
        else "cash"
    )


def _summary(
    output: Path,
    windows: dict[str, list[Result]],
    models: dict[Strategy, TrainedModel],
    audits: dict[Strategy, dict[str, object]],
    selected: Strategy | None,
    error: str | None,
) -> None:
    rows = [
        "# 실험 06 — 규칙 후보의 수익성 학습",
        "",
        "이미 관찰한 구간의 연구 비교입니다. 새 최종 검증·실거래·자동 승격은 하지 않았습니다.",
        "",
        f"선택: {STRATEGY_LABELS[selected] if selected else '미완료'}",
        "",
        "## 학습 자료",
        "",
        "| 규칙 | 학습 표본 | 검증 표본 | 학습 여부 |",
        "|---|---:|---:|---|",
    ]
    for rule, audit in audits.items():
        rows.append(
            f"| {STRATEGY_LABELS[rule]} | {audit['training']} | {audit['validation']} | {audit['status']} |"
        )
    rows += [
        "",
        "표본은 겹치는 가상 거래를 포함하며 독립 거래 수가 아닙니다. 상세 사건 묶음 수는 audit.json에 기록합니다.",
        "",
        "## 최적 가중치",
        "",
        "| 모델 | 실제 epoch | 복원 epoch | 최저 검증 BCE |",
        "|---|---:|---:|---:|",
    ]
    for name, model in models.items():
        m = model.metadata
        rows.append(
            f"| {STRATEGY_LABELS[name]} | {m.epochs} | {m.checkpoint_epoch} | {min(m.validation_losses):.4f} |"
        )
    rows += [
        "",
        "## 수익률 비교",
        "",
        "| 구간 | 전략 | 기본 순수익률 | 최저 순수익률(4조건) | 최악 낙폭 | 기본 청산 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for window_name, results in windows.items():
        for strategy in dict.fromkeys(r.strategy for r in results):
            group = _four(results, strategy)
            rows.append(
                f"| {window_name} | {STRATEGY_LABELS[strategy]} | {group[0].net_return:.2%} | "
                f"{min(r.net_return for r in group):.2%} | {max(r.max_drawdown for r in group):.2%} | {group[0].natural_exits} |"
            )
    rows += [
        "",
        "## 한계",
        "",
        f"실행 미완료: {error}"
        if error
        else "예정된 연구 재비교 완료. 수익성 입증이나 승격이 아닙니다.",
        "기본 비용은 편도 수수료 0.05%·슬리피지 0.1%, 원금은 구간별 100만 원입니다.",
        "최대 12시간 보유 제한은 새 전략과 그 대조군에만 적용했습니다. 이전 규칙·혼합 전략은 변경하지 않았습니다.",
        "12시간은 정상 신호 기준이며 체결 지연·위험 청산·구간 종료 시 실제 보유 시간은 달라질 수 있습니다.",
        "학습용 후보마다 초기화한 계좌와 실제 연속 계좌의 고점·위험 중단 상태는 다를 수 있습니다.",
        "0%는 미거래일 수 있으며 구간별 수익률을 연간 수익으로 합산하지 않습니다.",
        "2026년 미관측 평가 데이터의 기존 결측은 해결되지 않았습니다. 최대 낙폭은 시가·종가 기준이며 10% 상한 보장은 아닙니다.",
        "",
        "구간(UTC, 종료 제외): selection-1 2025-01-11~03-01, selection-2 2025-04-09~06-01, selection-3 2025-07-09~09-01, observed-2026 2026-01-13~03-01.",
        "",
    ]
    rows += [f"- [{name} 상세]({name}/summary.md)" for name in windows]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(rows) + "\n")


def run_meta_study(
    training_paths: list[Path],
    selection_paths: list[Path],
    test_path: Path,
    old_experiment: Path,
    output: Path,
) -> None:
    if len(training_paths) != 3 or len(selection_paths) != 3:
        raise ValueError("학습 3개와 선택 3개 데이터가 필요합니다")
    output.mkdir(parents=True, exist_ok=False)
    models: dict[Strategy, TrainedModel] = {}
    windows: dict[str, list[Result]] = {}
    audits: dict[Strategy, dict[str, object]] = {}
    selected: Strategy | None = None
    costs = Costs.model_validate(
        dict(
            buy_fee=".0005",
            sell_fee=".0005",
            buy_slippage=".001",
            sell_slippage=".001",
            min_notional="5000",
            quantity_step=".00000001",
        )
    )
    try:
        write_json(
            output / "protocol.json",
            dict(
                experiment="06",
                training=TRAINING,
                selection=SELECTION,
                observed_test=TESTS[0],
                candidates=META_COMPONENTS,
                boundary=BOUNDARY.isoformat(),
                min_training=50,
                min_validation=20,
                both_classes_required=True,
                entry_score=".50",
                max_epochs=100,
                patience=10,
                max_hold_hours=12,
                label="candidate_net_profit_after_costs",
                capital="1000000",
                costs=costs.model_dump(mode="json"),
                eligibility={"max_drawdown": ".10", "halted": False, "min_base_exits": 3},
                ranking="max worst net return, min worst drawdown, strategy ID",
                evaluation_status="previously_observed_recomparison",
                created_at=datetime.now(UTC).isoformat(),
                **source_identity(),
            ),
        )
        loaded = [
            _read(path, interval) for path, interval in zip(training_paths, TRAINING, strict=True)
        ]
        write_json(
            output / "training-data.json",
            [
                {"path": str(path.resolve()), "sha256": digest}
                for path, (_, digest) in zip(training_paths, loaded, strict=True)
            ],
        )
        for rule in TIMED_RULES.values():
            original = [event_samples(candles, rule, costs) for candles, _ in loaded]
            write_json(
                output / f"events-{rule}.json",
                [
                    {
                        "signal_time": signal.isoformat(),
                        "label_time": end.isoformat(),
                        "label": int(label),
                    }
                    for sample in original
                    for signal, end, label in zip(
                        sample.signal_times, sample.label_times, sample.labels.tolist(), strict=True
                    )
                ],
            )
            try:
                training, validation = split_samples(original, BOUNDARY)
            except ValueError:
                audits[rule] = {
                    "training": sum(
                        t < BOUNDARY for sample in original for t in sample.label_times
                    ),
                    "validation": sum(
                        t >= BOUNDARY for sample in original for t in sample.signal_times
                    ),
                    "status": "시간순 학습/검증 분리 불가",
                    "original": sample_counts(original),
                }
                continue
            tc, vc = sample_counts(training), sample_counts(validation)
            eligible = training_eligible(tc, vc)
            audits[rule] = {
                "training": tc["samples"],
                "validation": vc["samples"],
                "training_counts": tc,
                "validation_counts": vc,
                "purged_samples": sum(len(s.labels) for s in original)
                - tc["samples"]
                - vc["samples"],
                "last_training_label": max(t for s in training for t in s.label_times).isoformat(),
                "first_validation_signal": min(
                    t for s in validation for t in s.signal_times
                ).isoformat(),
                "status": "학습" if eligible else "표본 수 또는 클래스 부족으로 제외",
            }
            if not eligible:
                continue
            for name, (architecture, source_rule) in META_COMPONENTS.items():
                if source_rule != rule:
                    continue
                destination = output / "models" / name
                fitted = train_model(
                    training,
                    architecture,
                    destination,
                    epochs=100,
                    validation=validation,
                    patience=10,
                )
                restored = load_model(destination)
                if not torch.equal(
                    fitted.probabilities(validation[0].features),
                    restored.probabilities(validation[0].features),
                ):
                    raise ValueError("메타 모델 재로딩 예측 불일치")
                write_json(
                    destination / "task.json",
                    {
                        "task": "candidate_net_profit",
                        "strategy": name,
                        "rule": rule,
                        "max_hold_hours": 12,
                        "entry_score": ".50",
                        "costs": costs.model_dump(mode="json"),
                        "model_sha256": restored.metadata.checkpoint_sha256,
                    },
                )
                models[name] = restored
        write_json(output / "audit.json", audits)
        baseline_models = frozen_models(old_experiment, output / "baseline")
        write_json(
            output / "training-complete.json",
            {
                "models": {name: m.metadata.checkpoint_sha256 for name, m in models.items()},
                "baselines": {
                    name: m.metadata.checkpoint_sha256 for name, m in baseline_models.items()
                },
            },
        )

        def evaluate(
            path: Path, interval: tuple[str, str, str], name: str, strategies: tuple[Strategy, ...]
        ) -> None:
            candles, _ = _read(path, interval)
            start = _date(interval[1])
            predictions = _prediction_artifacts(
                baseline_models, candles, start, output / "predictions" / name
            )
            for strategy in strategies:
                if strategy in HYBRID_COMPONENTS:
                    predictions[strategy] = predictions[HYBRID_COMPONENTS[strategy][0]]
                if strategy in models:
                    model = models[strategy]
                    scores = {
                        time: p for time, p in model.predict(candles).items() if time >= start
                    }
                    predictions[strategy] = scores
                    write_json(
                        output / "predictions" / name / f"{strategy}.json",
                        {
                            "task": "candidate_net_profit",
                            "rule": META_COMPONENTS[strategy][1],
                            "model_sha256": model.metadata.checkpoint_sha256,
                            "probabilities": {
                                time.isoformat(): str(p) for time, p in scores.items()
                            },
                        },
                    )
            windows[name] = compare(
                path,
                start,
                Decimal(1000000),
                costs,
                output / name,
                strategies=strategies,
                predictions=predictions,
            )

        for index, (path, interval) in enumerate(zip(selection_paths, SELECTION, strict=True), 1):
            evaluate(path, interval, f"selection-{index}", (*META_CONTROLS, *models))
        selected = choose_meta(windows, tuple(models))
        write_json(
            output / "selection.json",
            {
                "selected": selected,
                "test_evaluated": False,
                "scores": {
                    name: dict(
                        zip(
                            ("eligible", "worst_return", "max_drawdown", "base_exits"),
                            (
                                str(v) if isinstance(v, Decimal) else v
                                for v in candidate_score(windows, name)
                            ),
                            strict=True,
                        )
                    )
                    for name in models
                },
            },
        )
        evaluate(
            test_path, TESTS[0], "observed-2026", tuple(dict.fromkeys((*META_CONTROLS, selected)))
        )
        write_json(
            output / "status.json",
            {
                "comparison_completed": True,
                "trained_models": len(models),
                "new_holdout_evaluated": False,
                "profitability_validated": False,
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError) as error:
        write_json(
            output / "failure.json", {"error": str(error), "completed_windows": list(windows)}
        )
        _summary(output, windows, models, audits, selected, str(error))
        raise
    _summary(output, windows, models, audits, selected, None)
