"""Offline experiment 03: two trained models, frozen selection, unseen evaluation."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import torch
from torch.nn import functional as F

from evergreen.market import Candle, load_dataset, write_json
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments.rules import REVERSION_WINDOWS, WINDOWS, _four
from evergreen.research.learning.models import TrainedModel, load_model, make_samples, train_model
from evergreen.research.report import STRATEGY_LABELS, compare, source_identity
from evergreen.strategies import ML_STRATEGIES, Strategy

TRAINING = tuple(WINDOWS.values())
SELECTION = tuple(REVERSION_WINDOWS.values())
TESTS = (("2026-01-05", "2026-01-13", "2026-03-01"), ("2026-04-05", "2026-04-13", "2026-06-01"))
BASELINES: tuple[Strategy, ...] = (
    "cash",
    "buy-hold",
    "sma-trend-v0",
    "sma-slow-v1",
    "breakout-v1",
    "band-reversion-v1",
    "rsi-rebound-v1",
)
COMPARISONS: tuple[Strategy, ...] = ("sma-trend-v0", "sma-slow-v1", "rsi-rebound-v1")


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _read(path: Path, interval: tuple[str, str, str]) -> tuple[list[Candle], str]:
    quality_file = path / "quality.json"
    if quality_file.exists():
        quality = json.loads(quality_file.read_text())
        if isinstance(quality, dict) and quality.get("status") == "failed":
            raise ValueError(f"품질 검사에 실패한 데이터셋입니다: {path}")
    candles, digest = load_dataset(path)
    if candles[0].open_time != _date(interval[0]) or candles[-1].close_time != _date(interval[2]):
        raise ValueError("실험 03의 사전 지정 데이터 기간과 다릅니다")
    return candles, digest


def choose_model(windows: dict[str, list[Result]]) -> Strategy:
    if set(windows) != {"selection-1", "selection-2", "selection-3"}:
        raise ValueError("모델 선택 구간 3개가 필요합니다")
    scores = {
        model: (
            -min(row.net_return for rows in windows.values() for row in _four(rows, model)),
            max(row.max_drawdown for rows in windows.values() for row in _four(rows, model)),
            model,
        )
        for model in ML_STRATEGIES
    }
    return min(ML_STRATEGIES, key=scores.__getitem__)


def check_results(windows: dict[str, list[Result]], selected: Strategy) -> dict[str, bool]:
    expected = {"selection-1", "selection-2", "selection-3", "test-a", "test-b"}
    if set(windows) != expected or selected not in ML_STRATEGIES:
        raise ValueError("선택·최종 평가 전체 결과가 필요합니다")
    candidates = {name: _four(rows, selected) for name, rows in windows.items()}
    return {
        "positive": all(r.net_return > 0 for rows in candidates.values() for r in rows),
        "risk": all(
            not r.halted and r.max_drawdown <= Decimal("0.10")
            for rows in candidates.values()
            for r in rows
        ),
        "beats_baselines": all(
            row.net_return > baseline.net_return
            for name, rows in candidates.items()
            for strategy in COMPARISONS
            for row, baseline in zip(rows, _four(windows[name], strategy), strict=True)
        ),
        "exit_count": sum(candidates[name][0].natural_exits for name in ("test-a", "test-b")) >= 10,
    }


def _prediction_artifacts(
    models: dict[Strategy, TrainedModel], candles: list[Candle], start: datetime, output: Path
) -> dict[Strategy, dict[datetime, Decimal]]:
    output.mkdir(parents=True, exist_ok=False)
    predictions: dict[Strategy, dict[datetime, Decimal]] = {}
    samples = make_samples(candles)
    mask = torch.tensor([time >= start for time in samples.signal_times], dtype=torch.bool)
    labels = samples.labels[mask]
    for name, model in models.items():
        # Future labels are used only for diagnostics, never as prediction inputs.
        scores = {time: p for time, p in model.predict(candles).items() if time >= start}
        predictions[name] = scores
        write_json(
            output / f"{name}.json",
            {
                "model_sha256": model.metadata.checkpoint_sha256,
                "probabilities": {time.isoformat(): str(p) for time, p in scores.items()},
            },
        )
        predicted = model.probabilities(samples.features[mask]).clamp(1e-7, 1 - 1e-7)
        constant = torch.full_like(labels, min(max(model.metadata.positive_rate, 1e-7), 1 - 1e-7))
        write_json(
            output / f"{name}.metrics.json",
            {
                "samples": len(labels),
                "bce": F.binary_cross_entropy(predicted, labels).item(),
                "constant_training_rate_bce": F.binary_cross_entropy(constant, labels).item(),
                "label_positive_rate": labels.mean().item(),
            },
        )
    return predictions


def _summary(
    output: Path,
    selected: Strategy | None,
    completed: dict[str, list[Result]],
    checks: dict[str, bool],
    error: str | None,
) -> None:
    verdict = (
        "검증 미완료" if error else ("연구 조건 통과" if all(checks.values()) else "수익성 미검증")
    )
    rows = [
        "# 딥러닝 실험 03 결과",
        "",
        f"판정: **{verdict}**",
        "",
        "실제 PyTorch MLP·1D CNN을 학습한 오프라인 연구입니다. 실거래·자동 승격은 없습니다.",
        "학습: 2024년 세 구간 / 모델 선택: 2025년 세 구간 / 최종 평가: 2026년 두 구간.",
        "모의 원금은 구간별 100만 원, 편도 수수료 0.05%·슬리피지 0.1%입니다.",
        "기본·슬리피지 2배·비용 2배·한 봉 추가 지연을 비교합니다.",
        "",
    ]
    rows.extend(
        [
            "## 실제 학습 기록",
            "",
            "| 모델 | 학습 표본 | 첫 epoch 손실 | 최종 epoch 손실 |",
            "|---|---:|---:|---:|",
        ]
    )
    for model_name in ML_STRATEGIES:
        metadata_file = output / "models" / model_name / "model.json"
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text())
            rows.append(
                f"| {STRATEGY_LABELS[model_name]} | {metadata['training_samples']} | "
                f"{metadata['losses'][0]:.4f} | {metadata['losses'][-1]:.4f} |"
            )
    rows.extend(["", "훈련 손실 감소는 미관측 수익성의 증거가 아닙니다.", ""])
    if selected:
        rows.extend(
            [
                f"선정 모델: {STRATEGY_LABELS[selected]}",
                "",
                "| 구간 | 기본 순수익률 | 네 조건 최저 수익률 | 네 조건 최대 낙폭 | 기본 청산 횟수 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        labels = {
            "selection-1": "모델 선택 1",
            "selection-2": "모델 선택 2",
            "selection-3": "모델 선택 3",
            "test-a": "최종 평가 A",
            "test-b": "최종 평가 B",
        }
        for name, results in completed.items():
            items = _four(results, selected)
            rows.append(
                f"| {labels[name]} | {items[0].net_return:.2%} | "
                f"{min(r.net_return for r in items):.2%} | "
                f"{max(r.max_drawdown for r in items):.2%} | {items[0].natural_exits} |"
            )
    if error:
        rows.extend(
            ["", f"중단 원인: `{error}`", "누락된 평가를 성공이나 수익률 0%로 간주하지 않습니다."]
        )
    rows.extend(
        [
            "",
            "## 한계와 상세 기록",
            "",
            "일부 구간 수익·분류 성능은 미래 수익의 증거가 아닙니다. 기간별 수익률을 연간 수익으로 합산하지 않습니다.",
            "시가·종가 기반 낙폭이며 봉 내부 위험·실제 호가 유동성·부분 체결은 반영하지 않습니다.",
            "2025년은 이미 관찰한 후보 선택 자료입니다. 2026년 평가도 실행 후에는 미관측 자료가 아닙니다.",
            "분류 레이블은 12시간 수익이지만 실제 매도는 신호·위험 중단에 따릅니다.",
            "딥러닝 모델이 규칙 기반보다 좋다는 전제는 없습니다. 체크포인트·정규화·BCE·기준 전략 결과를 함께 확인하세요.",
            "",
        ]
    )
    rows.extend(f"- [{name} 비교 결과]({name}/summary.md)" for name in completed)
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(rows) + "\n")


def run_deep_study(
    training: list[Path], selection: list[Path], tests: list[Path], output: Path
) -> dict[str, bool]:
    if (len(training), len(selection), len(tests)) != (3, 3, 2):
        raise ValueError("학습 3개·선택 3개·최종 평가 2개 데이터셋이 필요합니다")
    output.mkdir(parents=True, exist_ok=False)
    completed: dict[str, list[Result]] = {}
    selected: Strategy | None = None
    checks: dict[str, bool] = {}
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
                "experiment": "03",
                "training": TRAINING,
                "selection": SELECTION,
                "tests": TESTS,
                "models": ML_STRATEGIES,
                "epochs": 20,
                "seed": 17,
                "lookback": 32,
                "horizon": 12,
                "entry": "0.60",
                "exit": "0.45",
                "comparisons": COMPARISONS,
                "costs": costs.model_dump(mode="json"),
                "capital": "1000000",
                "created_at": datetime.now(UTC).isoformat(),
                **source_identity(),
            },
        )
        loaded = [_read(path, interval) for path, interval in zip(training, TRAINING, strict=True)]
        samples = [make_samples(candles) for candles, _ in loaded]
        write_json(
            output / "training-data.json",
            [
                {
                    "path": str(path.resolve()),
                    "sha256": digest,
                    "samples": len(sample.labels),
                    "last_label": sample.label_times[-1].isoformat(),
                }
                for path, (_, digest), sample in zip(training, loaded, samples, strict=True)
            ],
        )
        models: dict[Strategy, TrainedModel] = {}
        for architecture in ML_STRATEGIES:
            directory = output / "models" / architecture
            fitted = train_model(samples, architecture, directory)
            restored = load_model(directory)
            if fitted.predict(loaded[-1][0]) != restored.predict(loaded[-1][0]):
                raise ValueError("저장·재로딩 후 예측이 달라졌습니다")
            models[architecture] = restored
        for index, (path, interval) in enumerate(zip(selection, SELECTION, strict=True), 1):
            name = f"selection-{index}"
            candles, _ = _read(path, interval)
            scores = _prediction_artifacts(
                models, candles, _date(interval[1]), output / "predictions" / name
            )
            completed[name] = compare(
                path,
                _date(interval[1]),
                Decimal(1000000),
                costs,
                output / name,
                strategies=(*BASELINES, *ML_STRATEGIES),
                predictions=scores,
            )
        selected = choose_model(completed)
        write_json(
            output / "selection.json",
            {
                "selected": selected,
                "selected_at": datetime.now(UTC).isoformat(),
                "test_evaluated": False,
                "model_sha256": models[selected].metadata.checkpoint_sha256,
                "selection_rule": "max worst net return, min max drawdown, id",
                "scores": {
                    model: {
                        "worst_return": str(
                            min(
                                r.net_return
                                for rows in completed.values()
                                for r in _four(rows, model)
                            )
                        ),
                        "max_drawdown": str(
                            max(
                                r.max_drawdown
                                for rows in completed.values()
                                for r in _four(rows, model)
                            )
                        ),
                    }
                    for model in ML_STRATEGIES
                },
            },
        )
        for name, path, interval in zip(("test-a", "test-b"), tests, TESTS, strict=True):
            candles, _ = _read(path, interval)
            scores = _prediction_artifacts(
                {selected: models[selected]},
                candles,
                _date(interval[1]),
                output / "predictions" / name,
            )
            completed[name] = compare(
                path,
                _date(interval[1]),
                Decimal(1000000),
                costs,
                output / name,
                strategies=(*BASELINES, selected),
                predictions=scores,
            )
        checks = check_results(completed, selected)
        write_json(
            output / "verdict.json",
            {
                "complete": True,
                "checks": checks,
                "research_passed": all(checks.values()),
                "live_enabled": False,
            },
        )
        _summary(output, selected, completed, checks, None)
        return checks
    except (ValueError, OSError, RuntimeError, ArithmeticError) as error:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "error": str(error),
                "completed_windows": list(completed),
                "selected": selected,
            },
        )
        _summary(output, selected, completed, checks, str(error))
        raise
