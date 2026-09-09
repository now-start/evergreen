"""Experiment 15: purged, rolling-window breakout trade profitability models."""

import argparse
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Literal

import torch

from evergreen.market import write_json
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.experiments.regime import (
    SCENARIOS,
    Evaluation,
    joined_evaluations,
    merge_samples,
    period_samples,
)
from evergreen.research.experiments.regime_data import contiguous_blocks, quarters
from evergreen.research.experiments.regime_stability import SEEDS
from evergreen.research.learning.breakout_meta import (
    breakout_events,
    eligible,
    payoff_threshold,
    payoff_weights,
)
from evergreen.research.learning.meta import sample_counts
from evergreen.research.learning.models import (
    FeatureSet,
    Samples,
    TrainedModel,
    feature_warmup,
    load_model,
    train_model,
)
from evergreen.research.learning.regime import sample_identity
from evergreen.research.report import source_identity
from evergreen.strategies import Strategy

MODELS = ("mlp-v1", "cnn-v1")


def costs(fee: int = 1, slip: int = 1) -> Costs:
    return Costs(
        buy_fee=Decimal(".0005") * fee,
        sell_fee=Decimal(".0005") * fee,
        buy_slippage=Decimal(".001") * slip,
        sell_slippage=Decimal(".001") * slip,
        min_notional=Decimal(5000),
        quantity_step=Decimal(".00000001"),
    )


def evaluate(
    parts: dict[str, list[Evaluation]],
    output: Path,
    *,
    models: tuple[str, ...] = MODELS,
    exit_lookbacks: dict[str, int] | None = None,
    cooldowns: dict[str, int] | None = None,
    risk_budget_models: tuple[str, ...] = (),
    failure_exit_models: tuple[str, ...] = (),
    failure_exit_confirmations: dict[str, int] | None = None,
    failure_exit_buffer_models: tuple[str, ...] = (),
    trailing_exit_models: tuple[str, ...] = (),
    trailing_profit_only_models: tuple[str, ...] = (),
    trailing_adaptive_models: tuple[str, ...] = (),
    rejection_latch_models: tuple[str, ...] = (),
    entry_stop_models: tuple[str, ...] = (),
) -> None:
    candidates = ("breakout-v1", "cash", "prior", *models)
    if set(parts) != set(candidates):
        raise ValueError("평가 후보가 누락됐습니다")
    if exit_lookbacks is not None and set(exit_lookbacks) - set(models):
        raise ValueError("청산 채널 변경은 명시된 연구 후보에만 허용합니다")
    if cooldowns is not None and set(cooldowns) - set(models):
        raise ValueError("재진입 대기는 명시된 연구 후보에만 허용합니다")
    if set(risk_budget_models) - set(models):
        raise ValueError("위험 여유 검사는 명시된 연구 후보에만 허용합니다")
    if set(failure_exit_models) - set(models):
        raise ValueError("돌파 실패 청산은 명시된 연구 후보에만 허용합니다")
    if failure_exit_confirmations is not None and set(failure_exit_confirmations) - set(
        failure_exit_models
    ):
        raise ValueError("돌파 실패 확인은 해당 청산 후보에만 허용합니다")
    if set(failure_exit_buffer_models) - set(failure_exit_models):
        raise ValueError("돌파 실패 변동 폭은 해당 청산 후보에만 허용합니다")
    if set(trailing_exit_models) - set(models):
        raise ValueError("고점 추적 청산은 명시된 연구 후보에만 허용합니다")
    if set(trailing_profit_only_models) - set(trailing_exit_models):
        raise ValueError("상승 후 활성 조건은 고점 추적 청산 후보에만 허용합니다")
    if set(trailing_adaptive_models) - set(trailing_profit_only_models):
        raise ValueError("가변 추적 거리는 상승 후 활성 청산 후보에만 허용합니다")
    if set(rejection_latch_models) - set(models):
        raise ValueError("거절 기록은 명시된 연구 후보에만 허용합니다")
    if set(entry_stop_models) - set(models):
        raise ValueError("고정 손절은 명시된 연구 후보에만 허용합니다")
    joined = {name: joined_evaluations(values) for name, values in parts.items()}
    ranges = [[(p.start, p.bars[-1].close_time) for p in rows] for rows in joined.values()]
    if not ranges[0] or any(r != ranges[0] for r in ranges):
        raise ValueError("거래 수익성 후보의 평가 구간이 다릅니다")
    for index in range(len(ranges[0])):
        directory = output / "continuous" / f"block-{index:03d}"
        directory.mkdir(parents=True)
        rows = []
        for scenario, fee, slip, delay in SCENARIOS:
            for name in candidates:
                part = joined[name][index]
                filtered = name not in ("cash", "breakout-v1")
                strategy: Strategy = (
                    "regime-mlp-v1" if filtered else "cash" if name == "cash" else "breakout-v1"
                )
                result = run_backtest(
                    part.bars,
                    part.start,
                    Decimal(1000000),
                    costs(fee, slip),
                    strategy,
                    extra_delay_bars=delay,
                    regimes=part.predictions if filtered else None,
                    regime_policy="breakout-filter" if filtered else "routing",
                    breakout_exit_lookback=(exit_lookbacks or {}).get(name, 48),
                    breakout_cooldown_hours=(cooldowns or {}).get(name, 0),
                    breakout_risk_budget=name in risk_budget_models,
                    breakout_failure_exit=name in failure_exit_models,
                    breakout_failure_confirmations=(failure_exit_confirmations or {}).get(name, 1),
                    breakout_failure_buffer=name in failure_exit_buffer_models,
                    breakout_trailing_exit=name in trailing_exit_models,
                    breakout_trailing_profit_only=name in trailing_profit_only_models,
                    breakout_trailing_adaptive=name in trailing_adaptive_models,
                    breakout_rejection_latch=name in rejection_latch_models,
                    breakout_entry_stop=name in entry_stop_models,
                )
                write_json(directory / f"{scenario}.{name}.json", result.model_dump(mode="json"))
                rows.append(
                    {
                        "candidate": name,
                        "scenario": scenario,
                        **result.model_dump(
                            mode="json", exclude={"fills", "equity_curve", "rejections"}
                        ),
                    }
                )
        write_json(directory / "results.json", rows)


def summarize(
    output: Path, *, models: tuple[str, ...] = MODELS, seeds: tuple[int, ...] = SEEDS
) -> dict[str, Any]:
    candidates = ("breakout-v1", "cash", "prior", *models)
    rows = []
    passed = {name: True for name in models}
    reference = None
    for seed in seeds:
        groups = [
            json.loads(p.read_text())
            for p in sorted((output / f"seed-{seed}").glob("continuous/*/results.json"))
        ]
        if not groups or any(not g for g in groups):
            raise ValueError("빈 거래 수익성 평가 결과입니다")
        ranges = [(g[0]["start"], g[0]["end"]) for g in groups]
        if not groups or (reference is not None and reference != ranges):
            raise ValueError("시드별 거래 수익성 평가 범위 불일치")
        reference = ranges
        expected = {(c, s) for c in candidates for s, _, _, _ in SCENARIOS}
        for group in groups:
            if (
                len(group) != len(expected)
                or {(r["candidate"], r["scenario"]) for r in group} != expected
                or len({(r["start"], r["end"]) for r in group}) != 1
            ):
                raise ValueError("후보·비용·구간 결과가 누락되거나 중복됐습니다")
        for scenario, _, _, _ in SCENARIOS:
            by_candidate = {
                name: [
                    next(r for r in g if r["candidate"] == name and r["scenario"] == scenario)
                    for g in groups
                ]
                for name in candidates
            }
            for name, group in by_candidate.items():
                returns = [Decimal(r["net_return"]) for r in group]
                deltas = {
                    control: median(
                        value - Decimal(base["net_return"])
                        for value, base in zip(returns, by_candidate[control], strict=True)
                    )
                    for control in ("breakout-v1", "prior")
                }
                drawdown = max(Decimal(r["max_drawdown"]) for r in group)
                exits = sum(r["natural_exits"] for r in group)
                halted = sum(r["halted"] for r in group)
                positive = sum(r > 0 for r in returns)
                gate = bool(
                    all(d > 0 for d in deltas.values())
                    and positive > len(group) / 2
                    and drawdown <= Decimal(".10")
                    and not halted
                    and (scenario != "base" or exits >= 10)
                )
                if name in passed:
                    passed[name] &= gate
                rows.append(
                    {
                        "seed": seed,
                        "candidate": name,
                        "scenario": scenario,
                        "blocks": len(group),
                        "median_return": str(median(returns)),
                        "worst_return": str(min(returns)),
                        "worst_drawdown": str(drawdown),
                        "natural_exits": exits,
                        "halted_blocks": halted,
                        "positive_blocks": positive,
                        "active_blocks": sum(Decimal(r["exposure_ratio"]) > 0 for r in group),
                        "delta_breakout": str(deltas["breakout-v1"]),
                        "delta_prior": str(deltas["prior"]),
                        "gate": gate,
                    }
                )
    return {"historical": rows, "exploratory_gate": passed, "live_promotion": False}


def report(output: Path, results: dict[str, Any]) -> None:
    protocol = json.loads((output / "protocol.json").read_text())
    lines = [
        f"# 실험 {protocol['experiment']} — 원래 돌파 청산을 학습한 거래 수익성 모델",
        "",
        f"학습 {protocol['training_quarters']}분기 / 검증 {protocol['validation_quarters']}분기, 전체 자료창 8분기 고정.",
        f"진입 기준: {protocol.get('threshold_mode', 'fixed')}. payoff는 학습 평균손실/(평균이익+평균손실)이며 보정된 확률이나 최적 기준을 보장하지 않습니다.",
        "이미 관찰한 과거 자료의 탐색이며 실거래·신규 홀드아웃 검증이 아닙니다.",
        "22개 등 가용 연속 블록은 각각 독립 원금 100만 원이며, 아래 수익은 구간별 분포로 전체 누적 수익이 아닙니다.",
        "학습 부족 분기는 현금 대기하고 평가에는 포함합니다. 미거래 0%를 성공으로 해석하지 않습니다.",
        "prior는 학습 사건 양수 비율(가중 학습이면 양수 손익/절대 손익)의 상수 예측입니다. 모델과 같은 적격 분기에서만 사용합니다.",
        "weighted-score는 절대 거래 순수익 가중 BCE 점수 > 0.50입니다. 일반 승률이나 Brier 개선으로 해석하지 않습니다.",
        "",
        "| 시드 | 후보 | 비용 | 수익 중앙값 | 최악 낙폭 | 거래 블록/전체 | 청산 | 돌파 대비 차이 | 상수 대비 차이 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results["historical"]:
        lines.append(
            f"| {r['seed']} | {r['candidate']} | {r['scenario']} | {Decimal(r['median_return']):.2%} | {Decimal(r['worst_drawdown']):.2%} | {r['active_blocks']}/{r['blocks']} | {r['natural_exits']} | {Decimal(r['delta_breakout']):.2%}p | {Decimal(r['delta_prior']):.2%}p |"
        )
    lines += [
        "",
        f"사전 탐색 통과: {results['exploratory_gate']}. 실거래 승격 없음.",
        "사건 레이블은 겹치는 가상 거래이며 각각 계좌 고점이 초기화됩니다. 실제 연속 계좌와 위험 청산 시점이 다를 수 있습니다.",
        "부족한 분기·검증 표본·미완결 사건은 audits.json/events.json에 기록합니다. 시간순 분할은 실제 청산 봉 종료까지 제거합니다.",
        "10% 위험 중단은 체결 가격·비용·지연에 따른 실제 손실 상한을 보장하지 않습니다.",
        "",
    ]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


def run_study(
    source: Path,
    output: Path,
    *,
    validation_quarters: int = 1,
    threshold_mode: Literal["fixed", "payoff"] = "fixed",
    loss_weighting: Literal["uniform", "absolute-return"] = "uniform",
    model_family: Literal["neural", "linear"] = "neural",
    feature_set: FeatureSet = "short",
    overlap_adjusted: bool = False,
) -> None:
    warmup = max(169, feature_warmup(feature_set))
    if validation_quarters not in (1, 2):
        raise ValueError("검증 기간은 1 또는 2분기여야 합니다")
    if threshold_mode not in ("fixed", "payoff") or (
        threshold_mode == "payoff" and validation_quarters != 2
    ):
        raise ValueError("손익분기 비교는 검증 2분기 조건에서만 수행합니다")
    weighted = loss_weighting == "absolute-return"
    if loss_weighting not in ("uniform", "absolute-return") or (
        weighted and (validation_quarters != 2 or threshold_mode != "fixed")
    ):
        raise ValueError("가중 손실은 검증 2분기·고정 점수 기준에서만 수행합니다")
    if model_family not in ("neural", "linear") or (
        model_family == "linear" and validation_quarters != 2
    ):
        raise ValueError("선형 대조는 검증 2분기 조건에서만 수행합니다")
    architectures = ("linear-v1",) if model_family == "linear" else MODELS
    if feature_set != "short" and (not weighted or model_family != "neural"):
        raise ValueError("맥락 입력 대조는 가중 신경망 조건에서만 수행합니다")
    if overlap_adjusted and (
        feature_set != "short-200" or not weighted or model_family != "neural"
    ):
        raise ValueError("겹침 가중치 대조는 short-200 가중 신경망 조건에서만 수행합니다")
    candidates = ("breakout-v1", "cash", "prior", *architectures)
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "22"
            if overlap_adjusted
            else "21"
            if feature_set != "short"
            else "20"
            if model_family == "linear"
            else "19"
            if weighted
            else "17"
            if threshold_mode == "payoff"
            else "15"
            if validation_quarters == 1
            else "16",
            "seeds": SEEDS,
            "models": architectures,
            "model_family": model_family,
            "target": "net profit of one breakout entry until original rule/risk exit; censor settlement",
            "entry_threshold": ".50"
            if threshold_mode == "fixed"
            else "training_mean_loss/(training_mean_win+training_mean_loss)",
            "threshold_mode": "weighted-score" if weighted else threshold_mode,
            "loss_weighting": loss_weighting,
            "overlap_adjusted": overlap_adjusted,
            "training_quarters": 8 - validation_quarters,
            "validation_quarters": validation_quarters,
            "min_training_events": 50,
            "min_validation_events": 20,
            "min_training_disjoint": 20,
            "min_validation_disjoint": 5,
            "max_epochs": 100,
            "patience": 10,
            "feature_set": feature_set,
            "warmup": warmup,
            "base_costs": costs().model_dump(mode="json"),
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        blocks = contiguous_blocks(source, output / "datasets")
        sample_blocks: list[Samples] = []
        records: list[dict[str, object]] = []
        for index, block in enumerate(blocks):
            if len(block) < warmup + 1:
                continue
            print(f"돌파 거래 레이블: 블록 {index}, {len(block)}시간", flush=True)
            samples, events = breakout_events(block, costs(), feature_set=feature_set)
            sample_blocks.append(samples)
            records.extend({"block": index, **event} for event in events)
        write_json(output / "events.json", records)
        write_json(
            output / "event-counts.json",
            {
                "complete": sample_counts(sample_blocks),
                "censored": sum(r["status"] != "completed" for r in records),
            },
        )
        for seed in SEEDS:
            directory = output / f"seed-{seed}"
            directory.mkdir()
            parts: dict[str, list[Evaluation]] = {name: [] for name in candidates}
            audits = []
            for index, (start, end) in enumerate(quarters()):
                if index < 8:
                    continue
                training = period_samples(
                    sample_blocks,
                    quarters()[index - 8][0],
                    quarters()[index - validation_quarters][0],
                )
                validation = period_samples(
                    sample_blocks, quarters()[index - validation_quarters][0], start
                )
                tc, vc = sample_counts(training), sample_counts(validation)
                allowed = eligible(tc, vc)
                audit = {
                    "start": start.isoformat(),
                    "training": tc,
                    "validation": vc,
                    "training_sha256": sample_identity(training),
                    "validation_sha256": sample_identity(validation),
                    "status": "trained" if allowed else "cash_insufficient_events_or_classes",
                }
                audits.append(audit)
                models: dict[str, TrainedModel] = {}
                fold = directory / "folds" / start.date().isoformat()
                fold.mkdir(parents=True)
                prior = Decimal(tc["positives"]) / tc["samples"] if allowed else Decimal(0)
                threshold = Decimal(".50")
                training_weights = validation_weights = None
                if allowed and weighted:
                    training_weights, weight_audit = payoff_weights(
                        records, training, overlap_adjusted=overlap_adjusted
                    )
                    validation_weights, validation_weight_audit = payoff_weights(
                        records, validation, overlap_adjusted=overlap_adjusted
                    )
                    if (
                        weight_audit["sample_sha256"] != audit["training_sha256"]
                        or validation_weight_audit["sample_sha256"] != audit["validation_sha256"]
                    ):
                        raise ValueError("가중 손실 표본 해시가 다릅니다")
                    audit["weights"] = weight_audit
                    audit["validation_weights"] = validation_weight_audit
                    prior = Decimal(str(weight_audit["weighted_prior"]))
                if allowed and threshold_mode == "payoff":
                    threshold, payoff = payoff_threshold(
                        records,
                        quarters()[index - 8][0],
                        quarters()[index - validation_quarters][0],
                    )
                    if payoff["sample_sha256"] != audit["training_sha256"]:
                        raise ValueError("손익분기 계산 사건이 학습 표본과 다릅니다")
                    audit["payoff"] = payoff
                audit["entry_threshold"] = str(threshold)
                if allowed:
                    print(f"거래 수익성 학습: 시드 {seed}, {start.date()}", flush=True)
                    for architecture in architectures:
                        path = fold / architecture
                        fitted = train_model(
                            training,
                            architecture,
                            path,
                            epochs=100,
                            validation=validation,
                            patience=10,
                            seed=seed,
                            sample_weights=training_weights,
                            validation_weights=validation_weights,
                            feature_set=feature_set,
                        )
                        restored = load_model(path)
                        if not torch.equal(
                            fitted.probabilities(validation[0].features),
                            restored.probabilities(validation[0].features),
                        ):
                            raise ValueError("거래 모델 재로딩 예측 불일치")
                        write_json(
                            path / "task.json",
                            {
                                **audit,
                                "task": "breakout_net_profit",
                                "seed": seed,
                                "training_label_max": max(
                                    t for s in training for t in s.label_times
                                ).isoformat(),
                                "validation_label_max": max(
                                    t for s in validation for t in s.label_times
                                ).isoformat(),
                                "entry_threshold": str(threshold),
                            },
                        )
                        models[architecture] = restored
                for block in blocks:
                    evaluation_start = max(start, block[0].open_time + timedelta(hours=warmup))
                    evaluation_end = min(end, block[-1].close_time)
                    if evaluation_end - evaluation_start < timedelta(hours=24):
                        continue
                    bars = [
                        b
                        for b in block
                        if evaluation_start - timedelta(hours=warmup)
                        <= b.open_time
                        < evaluation_end
                    ]
                    required = {b.close_time for b in bars if b.close_time >= evaluation_start}
                    for name in candidates:
                        if name in models:
                            scores = models[name].predict(bars)
                            approvals = {
                                t: 2
                                if (
                                    scores[t] >= threshold
                                    if threshold_mode == "fixed" and not weighted
                                    else scores[t] > threshold
                                )
                                else 0
                                for t in required
                            }
                            write_json(
                                fold
                                / f"{evaluation_start.isoformat().replace(':', '-')}.{name}.json",
                                {t.isoformat(): str(scores[t]) for t in sorted(required)},
                            )
                        else:
                            approvals = {
                                t: 2
                                if name == "prior"
                                and (
                                    prior >= threshold
                                    if threshold_mode == "fixed" and not weighted
                                    else prior > threshold
                                )
                                else 0
                                for t in required
                            }
                        parts[name].append(Evaluation(evaluation_start, bars, approvals))
                # Classification on event outcomes is diagnostic only, never threshold selection.
                observed = period_samples(sample_blocks, start, end)
                if models and observed:
                    sample = merge_samples(observed)
                    metrics = {}
                    for name, model in models.items():
                        p = model.probabilities(sample.features)
                        metrics[name] = {
                            "events": len(p),
                            "brier": float(((p - sample.labels) ** 2).mean()),
                            "accuracy": float(((p >= 0.5) == sample.labels.bool()).float().mean()),
                            "positive_rate": float(sample.labels.mean()),
                            "prior_brier": float(((float(prior) - sample.labels) ** 2).mean()),
                            "score_semantics": "weighted_class_score_not_win_probability"
                            if weighted
                            else "win_probability_proxy",
                        }
                    write_json(fold / "classification.json", metrics)
            write_json(directory / "audits.json", audits)
            print(f"연속 계좌 비교: 시드 {seed}", flush=True)
            evaluate(parts, directory, models=architectures)
        results = summarize(output, models=architectures)
        write_json(output / "results.json", results)
        report(output, results)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "exploratory_gate": results["exploratory_gate"],
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError, KeyError, TypeError) as error:
        write_json(
            output / "failure.json",
            {"status": "failed", "error": str(error), "live_enabled": False},
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="돌파 거래 수익성의 시드별 비교")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-quarters", type=int, choices=(1, 2), default=1)
    parser.add_argument("--threshold-mode", choices=("fixed", "payoff"), default="fixed")
    parser.add_argument(
        "--loss-weighting", choices=("uniform", "absolute-return"), default="uniform"
    )
    parser.add_argument("--model-family", choices=("neural", "linear"), default="neural")
    parser.add_argument("--overlap-adjusted", action="store_true")
    parser.add_argument(
        "--feature-set", choices=("short", "short-200", "breakout-context"), default="short"
    )
    args = parser.parse_args()
    run_study(
        args.source,
        args.output,
        validation_quarters=args.validation_quarters,
        threshold_mode=args.threshold_mode,
        loss_weighting=args.loss_weighting,
        model_family=args.model_family,
        feature_set=args.feature_set,
        overlap_adjusted=args.overlap_adjusted,
    )


if __name__ == "__main__":
    main()
