"""Bounded hybrid search with frozen models and selection-before-evaluation artifacts."""

import hashlib
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, field_validator

from evergreen.market import utc_hour, write_json
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments.deep import (
    BASELINES,
    SELECTION,
    TESTS,
    _date,
    _prediction_artifacts,
    _read,
)
from evergreen.research.experiments.rules import _four
from evergreen.research.learning.models import TrainedModel, load_model
from evergreen.research.report import STRATEGY_LABELS, compare, source_identity
from evergreen.strategies import HYBRID_COMPONENTS, ML_STRATEGIES, Strategy

NEW_TEST = ("2026-07-01", "2026-07-09", "2026-09-01")
CONTROLS = (*BASELINES, *ML_STRATEGIES)


class _TrainingSplit(BaseModel):
    last_training_label: datetime
    first_validation_signal: datetime

    _hours = field_validator("last_training_label", "first_validation_signal")(utc_hour)


class _FrozenModels(BaseModel):
    early: dict[str, str]


def candidate_score(
    windows: dict[str, list[Result]], strategy: Strategy
) -> tuple[bool, Decimal, Decimal, int]:
    groups = [_four(rows, strategy) for rows in windows.values()]
    worst_return = min(r.net_return for group in groups for r in group)
    drawdown = max(r.max_drawdown for group in groups for r in group)
    exits = sum(group[0].natural_exits for group in groups)
    eligible = (
        exits >= 3
        and drawdown <= Decimal(".10")
        and not any(r.halted for group in groups for r in group)
    )
    return eligible, worst_return, drawdown, exits


def choose_hybrid(windows: dict[str, list[Result]]) -> Strategy:
    if set(windows) != {"selection-1", "selection-2", "selection-3"}:
        raise ValueError("혼합 후보 선택에는 지정된 3개 구간이 필요합니다")
    scores = {name: candidate_score(windows, name) for name in HYBRID_COMPONENTS}
    eligible = [name for name, score in scores.items() if score[0]]
    return (
        min(eligible, key=lambda name: (-scores[name][1], scores[name][2], name))
        if eligible
        else "cash"
    )


def frozen_models(experiment: Path, output: Path) -> dict[Strategy, TrainedModel]:
    split = _TrainingSplit.model_validate_json((experiment / "split.json").read_text())
    if not (
        _date("2024-01-01")
        <= split.last_training_label
        < split.first_validation_signal
        == _date("2024-12-01")
    ):
        raise ValueError("실험 04의 2024년 학습/검증 경계가 필요합니다")
    frozen = _FrozenModels.model_validate_json(
        (experiment / "training-complete.json").read_text()
    ).early
    if set(frozen) != set(ML_STRATEGIES):
        raise ValueError("고정 모델 목록에는 MLP와 CNN이 모두 필요합니다")
    models: dict[Strategy, TrainedModel] = {}
    for name in ML_STRATEGIES:
        source = experiment / "models" / "early" / name
        model = load_model(source)
        if (
            model.metadata.architecture != name
            or model.metadata.checkpoint_sha256 != frozen[name]
            or model.metadata.patience != 10
            or model.metadata.checkpoint_epoch != model.metadata.best_epoch
        ):
            raise ValueError("고정된 조기 종료 모델과 다릅니다")
        destination = output / "models" / name
        destination.mkdir(parents=True, exist_ok=False)
        for filename in ("model.json", "checkpoint.pt"):
            shutil.copyfile(source / filename, destination / filename)
        models[name] = load_model(destination)
    write_json(
        output / "model-origin.json",
        {
            "experiment": str(experiment.resolve()),
            "origin_files_sha256": {
                name: hashlib.sha256((experiment / name).read_bytes()).hexdigest()
                for name in ("protocol.json", "split.json", "training-complete.json")
            },
            "model_sha256": frozen,
        },
    )
    return models


def _summary(
    output: Path, windows: dict[str, list[Result]], selected: Strategy | None, failure: str | None
) -> None:
    rows = [
        "# 실험 05 — 규칙·딥러닝 혼합 비교",
        "",
        "연구용 모의 결과이며 실거래·자동 승격은 실행하지 않았습니다.",
        "",
        f"선택: {STRATEGY_LABELS[selected] if selected else '선택 미완료'}",
        "선택은 2025년의 이미 관찰한 세 구간만 사용했습니다. 2026년 재비교와 새 평가로 재선택하지 않습니다.",
        "기본 비용은 편도 수수료 0.05%·슬리피지 0.1%, 구간별 원금 100만 원입니다.",
        "",
        "## 후보 선택 결과",
        "",
        "| 후보 | 위험·거래 수 적격 | 최저 순수익률(12조건) | 최악 낙폭 | 기본 청산 합계 |",
        "|---|---|---:|---:|---:|",
    ]
    selections = {key: value for key, value in windows.items() if key.startswith("selection-")}
    if len(selections) == 3:
        for candidate in HYBRID_COMPONENTS:
            eligible, worst, dd, exits = candidate_score(selections, candidate)
            rows.append(
                f"| {STRATEGY_LABELS[candidate]} | {'적격' if eligible else '미달'} | {worst:.2%} | {dd:.2%} | {exits} |"
            )
    rows += [
        "",
        "## 선택 후보와 단독 전략 비교",
        "",
        "| 구간 | 전략 | 기본 순수익률 | 최저 순수익률(4조건) | 최악 낙폭 | 기본 청산 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    if selected is not None:
        for name, results in windows.items():
            for strategy in dict.fromkeys((selected, *CONTROLS)):
                group = _four(results, strategy)
                rows.append(
                    f"| {name} | {STRATEGY_LABELS[strategy]} | {group[0].net_return:.2%} | "
                    f"{min(r.net_return for r in group):.2%} | {max(r.max_drawdown for r in group):.2%} | {group[0].natural_exits} |"
                )
    rows += [
        "",
        "## 판정과 한계",
        "",
        f"검증 미완료: {failure}"
        if failure
        else "예정된 비교 완료. 단일 새 구간 평가만으로 장기 수익성을 검증했다고 보지 않습니다.",
        "선택 적격은 양의 수익을 뜻하지 않습니다. 적격 후보가 없으면 현금 유지입니다.",
        "0%는 미거래일 수 있습니다. 청산 횟수는 종료 정산 제외이며, 구간별 수익을 연간 수익으로 합산하지 않습니다.",
        "낙폭은 시가·종가 관측이며 10% 중단이 실제 손실 상한을 보장하지 않습니다.",
        "",
        "구간: selection-1/2/3은 2025-01-11~03-01 / 04-09~06-01 / 07-09~09-01 UTC.",
        "observed-2026은 2026-01-13~03-01, new-2026은 2026-07-09~09-01 UTC(모두 종료 제외).",
        "",
    ]
    rows += [f"- [{name} 상세 결과]({name}/summary.md)" for name in windows]
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(rows) + "\n")


def run_hybrid_study(
    experiment: Path, selection_paths: list[Path], test_paths: list[Path], output: Path
) -> None:
    if len(selection_paths) != 3 or len(test_paths) != 2:
        raise ValueError("선택 3개, 후속 평가 2개 데이터가 필요합니다")
    output.mkdir(parents=True, exist_ok=False)
    windows: dict[str, list[Result]] = {}
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
            {
                "experiment": "05",
                "candidates": HYBRID_COMPONENTS,
                "selection": SELECTION,
                "observed_test": TESTS[0],
                "new_test": NEW_TEST,
                "entry": "and",
                "exit": "or",
                "ranking": "max worst net return, then min worst drawdown, then strategy ID",
                "eligibility": {"max_drawdown": ".10", "halted": False, "min_base_exits": 3},
                "fallback": "cash",
                "costs": costs.model_dump(mode="json"),
                "capital": "1000000",
                "created_at": datetime.now(UTC).isoformat(),
                **source_identity(),
            },
        )
        models = frozen_models(experiment, output)

        def evaluate(
            path: Path, interval: tuple[str, str, str], name: str, strategies: tuple[Strategy, ...]
        ) -> None:
            candles, _ = _read(path, interval)
            start = _date(interval[1])
            probabilities = _prediction_artifacts(
                models, candles, start, output / "predictions" / name
            )
            for strategy in strategies:
                if strategy in HYBRID_COMPONENTS:
                    probabilities[strategy] = probabilities[HYBRID_COMPONENTS[strategy][0]]
            windows[name] = compare(
                path,
                start,
                Decimal(1000000),
                costs,
                output / name,
                strategies=strategies,
                predictions=probabilities,
            )

        for index, (path, interval) in enumerate(zip(selection_paths, SELECTION, strict=True), 1):
            evaluate(path, interval, f"selection-{index}", (*CONTROLS, *HYBRID_COMPONENTS))
        selected = choose_hybrid(windows)
        write_json(
            output / "selection.json",
            {
                "selected": selected,
                "test_evaluated": False,
                "models": {
                    name: model.metadata.checkpoint_sha256 for name, model in models.items()
                },
                "candidates": {
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
                    for name in HYBRID_COMPONENTS
                },
            },
        )
        for name, path, interval in zip(
            ("observed-2026", "new-2026"), test_paths, (TESTS[0], NEW_TEST), strict=True
        ):
            evaluate(path, interval, name, tuple(dict.fromkeys((*CONTROLS, selected))))
        write_json(
            output / "status.json",
            {
                "comparison_completed": True,
                "new_window_evaluated": True,
                "live_enabled": False,
                "profitability_validated": False,
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError, KeyError) as error:
        write_json(
            output / "failure.json", {"error": str(error), "completed_windows": list(windows)}
        )
        _summary(output, windows, selected, str(error))
        raise
    _summary(output, windows, selected, None)
