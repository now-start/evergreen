"""Experiment 23 phase A: equal-weight frozen MLP scores, never averaged account returns."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from evergreen.market import write_json
from evergreen.research.experiments.breakout_errors import analyze
from evergreen.research.experiments.breakout_meta import costs, evaluate, summarize
from evergreen.research.experiments.regime import Evaluation
from evergreen.research.experiments.regime_data import contiguous_blocks, quarters
from evergreen.research.learning.models import ModelMetadata
from evergreen.research.report import source_identity

MEMBERS = (17, 29, 43)
MODEL = "ensemble-mlp"


def mean_scores(
    members: list[dict[datetime, Decimal]], expected: set[datetime]
) -> dict[datetime, Decimal]:
    if len(members) != 3 or not expected or any(set(m) != expected for m in members):
        raise ValueError("앙상블 구성원·예측 시간 범위가 다릅니다")
    if any(not p.is_finite() or not 0 <= p <= 1 for m in members for p in m.values()):
        raise ValueError("앙상블 점수가 유효하지 않습니다")
    return {t: sum((m[t] for m in members), Decimal(0)) / 3 for t in sorted(expected)}


def run_ensemble(raw: Path, control: Path, candidate: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        content = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    write_json(
        output / "protocol.json",
        {
            "experiment": "23",
            "stage": "A",
            "members": MEMBERS,
            "additional_groups_if_pass": [[53, 67, 79], [97, 109, 127]],
            "aggregation": "mean_score_strictly_above_0.50",
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        blocks = contiguous_blocks(raw, output / "datasets")
        coverage = read(output / "datasets/coverage.json")
        reference_events = reference_protocol = None
        comparison = {}
        for name, source, experiment, adjusted in (
            ("control", control, "21", False),
            ("candidate", candidate, "22", True),
        ):
            protocol = read(source / "protocol.json")
            if (
                read(source / "status.json")["status"] != "completed_partial_coverage"
                or protocol["experiment"] != experiment
                or protocol["feature_set"] != "short-200"
                or protocol["warmup"] != 200
                or protocol["seeds"] != list(MEMBERS)
                or protocol["threshold_mode"] != "weighted-score"
                or protocol["loss_weighting"] != "absolute-return"
                or protocol["overlap_adjusted"] != adjusted
                or protocol["training_quarters"] != 6
                or protocol["validation_quarters"] != 2
                or Decimal(protocol["entry_threshold"]) != Decimal(".5")
                or protocol["base_costs"] != costs().model_dump(mode="json")
                or read(source / "datasets/coverage.json") != coverage
            ):
                raise ValueError("동결 모델의 자료·조건이 사전 계획과 다릅니다")
            shared_protocol = {
                k: v for k, v in protocol.items() if k not in ("experiment", "overlap_adjusted")
            }
            if reference_protocol is not None and shared_protocol != reference_protocol:
                raise ValueError("대조와 후보의 입력 소스·모델 조건이 다릅니다")
            reference_protocol = shared_protocol
            events = read(source / "events.json")
            if reference_events is not None and events != reference_events:
                raise ValueError("대조와 후보의 사건이 다릅니다")
            reference_events = events
            audits = [read(source / f"seed-{seed}/audits.json") for seed in MEMBERS]
            starts = [start.isoformat() for start, _ in quarters()[8:]]
            if any(a != audits[0] for a in audits) or [a["start"] for a in audits[0]] != starts:
                raise ValueError("앙상블 학습 분기·표본 감사 기록이 다릅니다")
            directory = output / name
            group = (
                directory / "seed-17"
            )  # Artifact slot, represents all three members, not seed 17 alone.
            group.mkdir(parents=True)
            write_json(
                directory / "protocol.json",
                {
                    **protocol,
                    "experiment": "23",
                    "members": MEMBERS,
                    "stage": "A",
                    "models": [MODEL],
                    "artifact_group_slot": 17,
                },
            )
            write_json(directory / "events.json", events)
            write_json(group / "audits.json", audits[0])
            parts: dict[str, list[Evaluation]] = {
                n: [] for n in ("breakout-v1", "cash", "prior", MODEL)
            }
            for audit, (start, end) in zip(audits[0], quarters()[8:], strict=True):
                if audit["status"] != "trained" or Decimal(audit["entry_threshold"]) != Decimal(
                    ".5"
                ):
                    raise ValueError("학습 완료 분기만 결합합니다")
                pieces = []
                for block in blocks:
                    begin, finish = (
                        max(start, block[0].open_time + timedelta(hours=200)),
                        min(end, block[-1].close_time),
                    )
                    if finish - begin < timedelta(hours=24):
                        continue
                    bars = [
                        b for b in block if begin - timedelta(hours=200) <= b.open_time < finish
                    ]
                    pieces.append((begin, bars))
                expected = {
                    b.close_time for begin, bars in pieces for b in bars if b.close_time >= begin
                }
                forecasts = []
                for seed in MEMBERS:
                    fold = source / f"seed-{seed}/folds/{start.date()}"
                    meta = ModelMetadata.model_validate(read(fold / "mlp-v1/model.json"))
                    task = read(fold / "mlp-v1/task.json")
                    checkpoint = fold / "mlp-v1/checkpoint.pt"
                    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
                    hashes[str(checkpoint.resolve())] = digest
                    if (
                        meta.seed != seed
                        or meta.architecture != "mlp-v1"
                        or meta.feature_set != "short-200"
                        or meta.checkpoint_sha256 != digest
                        or meta.loss_weighting != "sample"
                        or any(task.get(k) != v for k, v in audit.items())
                        or task["seed"] != seed
                        or datetime.fromisoformat(task["validation_label_max"]) >= start
                    ):
                        raise ValueError("동결 모델 메타데이터·학습 기록이 다릅니다")
                    scores: dict[datetime, Decimal] = {}
                    for path in sorted(fold.glob("*.mlp-v1.json")):
                        for t, value in read(path).items():
                            time = datetime.fromisoformat(t)
                            if time in scores:
                                raise ValueError("분기 안에 중복 예측이 있습니다")
                            scores[time] = Decimal(value)
                    forecasts.append(scores)
                average = mean_scores(forecasts, expected)
                prior = Decimal(audit["weights"]["weighted_prior"])
                if not prior.is_finite() or not 0 <= prior <= 1:
                    raise ValueError("상수 점수가 유효하지 않습니다")
                for begin, bars in pieces:
                    required = {b.close_time for b in bars if b.close_time >= begin}
                    (group / "folds" / str(start.date())).mkdir(parents=True, exist_ok=True)
                    write_json(
                        group
                        / "folds"
                        / str(start.date())
                        / f"{begin.isoformat().replace(':', '-')}.{MODEL}.json",
                        {t.isoformat(): str(average[t]) for t in sorted(required)},
                    )
                    for n in parts:
                        approvals = {
                            t: 2
                            if (
                                average[t] > Decimal(".5")
                                if n == MODEL
                                else n == "prior" and prior > Decimal(".5")
                            )
                            else 0
                            for t in required
                        }
                        parts[n].append(Evaluation(begin, bars, approvals))
            evaluate(parts, group, models=(MODEL,))
            result = summarize(directory, models=(MODEL,), seeds=(17,))
            write_json(directory / "results.json", result)
            write_json(
                directory / "status.json",
                {"status": "completed_partial_coverage", "live_enabled": False},
            )
            analyze(directory, directory / "errors", seeds=(17,), models=(MODEL,))
            comparison[name] = result
        write_json(output / "results.json", comparison)
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "live_enabled": False,
                "additional_groups_required": [
                    n for n, r in comparison.items() if r["exploratory_gate"][MODEL]
                ],
                "stability_proven": False,
            },
        )
    except (ValueError, OSError, RuntimeError, ArithmeticError, KeyError, TypeError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="동결 MLP 시드 점수 평균 비교")
    for arg in ("raw", "control", "candidate", "output"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    args = parser.parse_args()
    run_ensemble(args.raw, args.control, args.candidate, args.output)


if __name__ == "__main__":
    main()
