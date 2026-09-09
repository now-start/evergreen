"""Experiment 08b: purged walk-forward regime models and shared-cost historical simulations."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import torch

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.experiments.regime_data import contiguous_blocks, quarters
from evergreen.research.learning.models import Samples
from evergreen.research.learning.regime import (
    classification,
    make_regime_samples,
    split_samples,
    train_regime,
)
from evergreen.research.learning.regime_features import (
    FeatureSet,
    feature_channels,
    feature_warmup,
    regime_features,
)
from evergreen.research.report import SCENARIO_LABELS, STRATEGY_LABELS, source_identity
from evergreen.strategies import Strategy
from evergreen.strategies.regime import rule_regime, stabilize

STRATEGIES: tuple[Strategy, ...] = (
    "cash",
    "buy-hold",
    "sma-slow-v1",
    "breakout-v1",
    "regime-rules-v1",
    "regime-mlp-v1",
)
SCENARIOS = (
    ("base", 1, 1, 0),
    ("slippage-x2", 1, 2, 0),
    ("costs-x2", 2, 2, 0),
    ("delay-1h", 1, 1, 1),
)


@dataclass
class Evaluation:
    start: datetime
    bars: list[Candle]
    predictions: dict[datetime, int]


def merge_samples(samples: list[Samples]) -> Samples:
    if not samples:
        raise ValueError("학습·검증에 사용 가능한 표본이 없습니다")
    return Samples(
        torch.cat([s.features for s in samples]),
        torch.cat([s.labels for s in samples]),
        tuple(t for s in samples for t in s.signal_times),
        tuple(t for s in samples for t in s.label_times),
    )


def period_samples(samples: list[Samples], start: datetime, end: datetime) -> list[Samples]:
    return [
        split_samples(s, start, end)
        for s in samples
        if any(
            start <= t < end and label < end
            for t, label in zip(s.signal_times, s.label_times, strict=True)
        )
    ]


def joined_evaluations(parts: list[Evaluation]) -> list[Evaluation]:
    """Keep one uninterrupted portfolio across model retraining, never bridge missing data."""
    groups: list[Evaluation] = []
    for part in sorted(parts, key=lambda p: p.start):
        if groups and groups[-1].bars[-1].close_time == part.start:
            previous = groups[-1]
            overlap = {bar.open_time: bar for bar in previous.bars[-len(part.bars) :]}
            for bar in part.bars:
                if bar.open_time < part.start and bar.open_time in overlap:
                    if bar.model_dump(exclude={"fetched_at"}) != overlap[bar.open_time].model_dump(
                        exclude={"fetched_at"}
                    ):
                        raise ValueError("연속 평가의 겹치는 가격이 다릅니다")
            previous.bars.extend(bar for bar in part.bars if bar.open_time >= part.start)
            # At a retraining boundary only the newly frozen model owns that prediction.
            previous.predictions.update(part.predictions)
        else:
            groups.append(Evaluation(part.start, list(part.bars), dict(part.predictions)))
    return groups


def evaluate(part: Evaluation, output: Path) -> list[dict[str, object]]:
    output.mkdir(parents=True, exist_ok=False)
    required = {bar.close_time for bar in part.bars if bar.close_time >= part.start}
    rules = {
        bar.close_time: rule_regime(part.bars[max(0, i - 168) : i + 1])
        for i, bar in enumerate(part.bars)
    }
    schedules = {
        "regime-rules-v1": stabilize({t: value for t, value in rules.items() if t in required}),
        "regime-mlp-v1": stabilize(part.predictions),
    }
    write_json(
        output / "regimes.json",
        {
            name: {t.isoformat(): v for t, v in schedule.items() if t in required}
            for name, schedule in schedules.items()
        },
    )
    summaries: list[dict[str, object]] = []
    for scenario, fee, slip, delay in SCENARIOS:
        costs = Costs(
            buy_fee=Decimal(".0005") * fee,
            sell_fee=Decimal(".0005") * fee,
            buy_slippage=Decimal(".001") * slip,
            sell_slippage=Decimal(".001") * slip,
            min_notional=Decimal(5000),
            quantity_step=Decimal(".00000001"),
        )
        for strategy in STRATEGIES:
            schedule = schedules.get(strategy)
            result = run_backtest(
                part.bars,
                part.start,
                Decimal(1000000),
                costs,
                strategy,
                extra_delay_bars=delay,
                regimes={t: schedule[t] for t in required} if schedule is not None else None,
            )
            write_json(output / f"{scenario}.{strategy}.json", result.model_dump(mode="json"))
            summaries.append(
                {
                    "scenario": scenario,
                    **result.model_dump(
                        mode="json", exclude={"fills", "rejections", "equity_curve"}
                    ),
                }
            )
    write_json(output / "results.json", summaries)
    return summaries


def research_checks(groups: list[list[dict[str, object]]], missing: bool) -> dict[str, bool]:
    enough = bool(groups)
    positive = risk = superiority = enough
    exits = 0
    for group in groups:
        for scenario, _, _, _ in SCENARIOS:
            rows = {row["strategy"]: row for row in group if row["scenario"] == scenario}
            model = rows["regime-mlp-v1"]
            net = Decimal(str(model["net_return"]))
            positive &= net > 0
            risk &= not bool(model["halted"]) and Decimal(str(model["max_drawdown"])) <= Decimal(
                ".10"
            )
            superiority &= all(
                net > Decimal(str(rows[s]["net_return"]))
                for s in ("breakout-v1", "regime-rules-v1")
            )
            if scenario == "base":
                exits += int(str(model["natural_exits"]))
    return {
        "complete_coverage": not missing and enough,
        "positive": positive,
        "risk": risk,
        "superiority": superiority,
        "exit_count": exits >= 10,
        "live_promotion": False,
    }


def run_regime_study(
    source: Path,
    output: Path,
    *,
    balanced: bool = False,
    followups: bool = False,
    seed: int = 17,
    horizon: int = 24,
    feature_set: FeatureSet = "short",
) -> dict[str, bool]:
    warmup = max(169, feature_warmup(feature_set))
    if horizon not in (24, 72):
        raise ValueError("장세 예측 기간은 24 또는 72시간이어야 합니다")
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "08b",
            "balanced_training": balanced,
            "followup_ablations": followups,
            "validation_objective": "unweighted_cross_entropy",
            "architectures": [f"mlp-{32 * feature_channels(feature_set)}-32-16-3"],
            "feature_set": feature_set,
            "evaluation_warmup": warmup,
            "evaluation_start": "2022-01-01",
            "evaluation_end": "2026-09-01",
            "class_order": ["down", "sideways", "up"],
            "horizon_hours": horizon,
            "threshold": f"max(.003,past24h_std*sqrt({horizon}))",
            "confidence": 0.55,
            "confirmation": 3,
            "cooldown_hours": 6,
            "training_quarters": 7,
            "validation_quarters": 1,
            "max_epochs": 100,
            "patience": 10,
            "seed": seed,
            "regime_strategy": "up:breakout,sideways:band,down:cash",
            "guardrail": "10% permanent halt per continuous block",
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        blocks = contiguous_blocks(source, output / "datasets")
        sample_blocks = [
            make_regime_samples(b, horizon, feature_set)
            for b in blocks
            if len(b) >= feature_warmup(feature_set) + 2 + horizon
        ]
        pieces: list[Evaluation] = []
        skipped: list[dict[str, str]] = []
        quarter_results: list[list[dict[str, object]]] = []
        folds: list[dict[str, object]] = []
        for index, (start, end) in enumerate(quarters()):
            if index < 8:
                continue
            fold = output / "folds" / start.date().isoformat()
            fold.mkdir(parents=True)
            validation_start = quarters()[index - 1][0]
            training_start = quarters()[index - 8][0]
            training = period_samples(sample_blocks, training_start, validation_start)
            validation_parts = period_samples(sample_blocks, validation_start, start)
            if not training or not validation_parts:
                skipped.append(
                    {"start": start.isoformat(), "reason": "missing_training_or_validation"}
                )
                continue
            validation = merge_samples(validation_parts)
            if set(merge_samples(training).labels.tolist()) != {0, 1, 2} or set(
                validation.labels.tolist()
            ) != {0, 1, 2}:
                skipped.append({"start": start.isoformat(), "reason": "missing_class"})
                continue
            print(f"장세 MLP 학습·다음 분기 평가: {start.date()} ~ {end.date()}", flush=True)
            model = train_regime(
                training,
                validation,
                fold / "model",
                balanced=balanced,
                seed=seed,
                feature_set=feature_set,
            )
            prediction_records: list[dict[str, object]] = []
            metrics_x, metrics_y = [], []
            for block_index, block in enumerate(blocks):
                evaluation_start = max(start, block[0].open_time + timedelta(hours=warmup))
                evaluation_end = min(end, block[-1].close_time)
                if evaluation_end - evaluation_start < timedelta(hours=24):
                    continue
                bars = [
                    b
                    for b in block
                    if evaluation_start - timedelta(hours=warmup) <= b.open_time < evaluation_end
                ]
                predicted, probabilities = model.predict(bars)
                # No backfilled pre-fold predictions from a model trained after those signals.
                predicted = {t: value for t, value in predicted.items() if t >= evaluation_start}
                piece = Evaluation(evaluation_start, bars, predicted)
                pieces.append(piece)
                quarter_results.append(evaluate(piece, fold / f"part-{block_index:03d}"))
                _, times = regime_features(bars, feature_set)
                prediction_records.extend(
                    {"time": t.isoformat(), "probabilities": p.tolist()}
                    for t, p in zip(times, probabilities, strict=True)
                    if t >= evaluation_start
                )
                if evaluation_end - evaluation_start > timedelta(hours=horizon + 1):
                    samples = split_samples(
                        make_regime_samples(bars, horizon, feature_set),
                        evaluation_start,
                        evaluation_end,
                    )
                    metrics_x.append(model.probabilities(samples.features))
                    metrics_y.append(samples.labels)
            write_json(fold / "predictions.json", prediction_records)
            if metrics_y:
                metrics = classification(torch.cat(metrics_y), torch.cat(metrics_x), model.majority)
                write_json(fold / "classification.json", metrics)
                folds.append({"start": start.isoformat(), **metrics})
            else:
                skipped.append(
                    {"start": start.isoformat(), "reason": "no_eligible_evaluation_block"}
                )
        joined = joined_evaluations(pieces)
        continuous_results = []
        for index, part in enumerate(joined):
            print(
                f"원금 유지 연속 블록 평가: {part.start} ~ {part.bars[-1].close_time}", flush=True
            )
            continuous_results.append(evaluate(part, output / "continuous" / f"block-{index:03d}"))
            if followups:
                from evergreen.research.experiments.regime_followup import evaluate_ablations

                evaluate_ablations(part, output / "continuous" / f"block-{index:03d}")
        checks = research_checks(continuous_results, missing=True)
        write_json(
            output / "results.json",
            {
                "quarter_parts": quarter_results,
                "continuous_blocks": continuous_results,
                "classification": folds,
                "skipped": skipped,
                "checks": checks,
            },
        )
        write_summary(output, quarter_results, continuous_results, folds, skipped)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "checks": checks,
                "evaluated_hours": sum(
                    int((p.bars[-1].close_time - p.start).total_seconds() / 3600) for p in pieces
                ),
                "folds": len(folds),
                "continuous_blocks": len(joined),
                "live_enabled": False,
            },
        )
        return checks
    except (ValueError, OSError, RuntimeError, ArithmeticError) as error:
        write_json(
            output / "failure.json",
            {"status": "failed", "error": str(error), "live_enabled": False},
        )
        raise


def write_summary(
    output: Path,
    quarterly: list[list[dict[str, object]]],
    continuous: list[list[dict[str, object]]],
    folds: list[dict[str, object]],
    skipped: list[dict[str, str]],
) -> None:
    coverage = json.loads((output / "datasets" / "coverage.json").read_text())
    protocol = json.loads((output / "protocol.json").read_text())
    rows = [
        "# 장기 장세 전환 탐색 결과 (08b 공통 평가 엔진)",
        f"예측 기간 {protocol['horizon_hours']}시간 / 시드 {protocol['seed']} / 균형 학습 {protocol['balanced_training']}",
        "",
        "실거래 변경 없음. 결측으로 원안 08은 실패했으며, 아래는 모든 적격 연속 구간의 별도 탐색입니다.",
        f"수집 범위: 2020-01-01~2026-09-01 UTC. 결측 {len(coverage['quality']['missing'])}시간(워밍업 포함).",
        f"평가: 2022-01-01 이후 {len(folds)}개 분기 모델 / {len(quarterly)}개 분기 조각 / {len(continuous)}개 연속 운용 블록.",
        "모의 원금 각 100만 원. 편도 수수료 0.05%·슬리피지 0.1%, 네 비용/지연 조건.",
        "블록 사이 원금은 독립적이며 수익률을 합산·연율화하지 않습니다. 블록 안은 모델 재학습 시에도 고점·잔고·중단 상태를 유지합니다.",
        "단순 보유에는 낙폭 중단이 없습니다. 다른 전략의 10% 중단은 실제 손실 한도 보장이 아닙니다.",
        "",
        "## 연속 블록별 기본 비용 결과",
        "",
        "| 시작 UTC | 종료 UTC | 전략 | 순수익률 | 낙폭 | 청산 | 위험 중단 |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for group in continuous:
        for row in group:
            if row["scenario"] == "base":
                rows.append(
                    f"| {str(row['start'])[:10]} | {str(row['end'])[:10]} | {STRATEGY_LABELS[cast(Strategy, row['strategy'])]} | {Decimal(str(row['net_return'])):.2%} | {Decimal(str(row['max_drawdown'])):.2%} | {row['natural_exits']} | {'중단' if row['halted'] else '없음'} |"
                )
    rows += [
        "",
        "## 비용 스트레스 요약 (독립 블록 분포이며 누적 수익 아님)",
        "",
        "| 전략 | 비용 조건 | 수익 중앙값 | 최저 수익 | 최악 낙폭 | 양수 블록/전체 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for strategy in STRATEGIES:
        for scenario, _, _, _ in SCENARIOS:
            values = [
                row
                for group in continuous
                for row in group
                if row["strategy"] == strategy and row["scenario"] == scenario
            ]
            if not values:
                continue
            returns = sorted(Decimal(str(r["net_return"])) for r in values)
            median = (returns[(len(returns) - 1) // 2] + returns[len(returns) // 2]) / 2
            rows.append(
                f"| {STRATEGY_LABELS[strategy]} | {SCENARIO_LABELS[scenario]} | {median:.2%} | {min(returns):.2%} | {max(Decimal(str(r['max_drawdown'])) for r in values):.2%} | {sum(v > 0 for v in returns)}/{len(values)} |"
            )
    rows += [
        "",
        "## 분류 품질",
        "",
        "| 분기 | 정확도 | 학습 다수 클래스 정확도 | macro F1 |",
        "|---|---:|---:|---:|",
    ]
    for fold in folds:
        rows.append(
            f"| {str(fold['start'])[:10]} | {float(str(fold['accuracy'])):.2%} | {float(str(fold['majority_accuracy'])):.2%} | {float(str(fold['macro_f1'])):.3f} |"
        )
    rows += [
        "",
        "## 판정",
        "",
        "전체 기간 검증 미완료·수익성 미입증·실거래 승격 없음.",
        "단일 시드와 과거 일부 관찰 구간을 포함합니다. 예측 점수는 보정된 확률이 아닙니다.",
        "분기 조각별 상세 비용 결과·주문·원금 곡선은 folds/, 연속 결과는 continuous/에 있습니다.",
        f"학습/평가 제외: {json.dumps(skipped, ensure_ascii=False)}",
        "",
    ]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(rows))
