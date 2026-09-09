"""Experiment 24: one prespecified faster exit, unchanged breakout entries and risk."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from evergreen.market import write_json
from evergreen.research.experiments.breakout_meta import costs, evaluate, summarize
from evergreen.research.experiments.regime import SCENARIOS, Evaluation
from evergreen.research.experiments.regime_data import contiguous_blocks
from evergreen.research.report import source_identity

CANDIDATE = "exit24"


def run_study(raw: Path, reference: Path, output: Path, *, cooldown_hours: int = 0) -> None:
    if cooldown_hours not in (0, 24):
        raise ValueError("사전 지정 대기 시간만 허용합니다")
    candidate = "exit24-cooldown24" if cooldown_hours else CANDIDATE
    models = (CANDIDATE, candidate) if cooldown_hours else (CANDIDATE,)
    reference_experiment = "24" if cooldown_hours else "23"
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "25" if cooldown_hours else "24",
            "candidate": candidate,
            "cooldown_hours": cooldown_hours,
            "cooldown_clock": "last_filled_sell_to_new_signal",
            "exit_lookback": 24,
            "control_exit_lookback": 48,
            "entry_lookback": 168,
            "entry_buffer": ".001",
            "warmup": 200,
            "capital": "1000000",
            "base_costs": costs().model_dump(mode="json"),
            "artifact_group_slot": 17,
            "trained_models": 0,
            "live_enabled": False,
            **source_identity(),
        },
    )
    hashes = {}

    def read(path: Path) -> Any:
        data = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(data).hexdigest()
        return json.loads(data)

    try:
        blocks = contiguous_blocks(raw, output / "datasets")
        if read(reference / "status.json")["status"] != "completed_partial_coverage":
            raise ValueError("완료된 이전 실험 대조가 필요합니다")
        if read(reference / "protocol.json")["experiment"] != reference_experiment:
            raise ValueError("사전 지정 이전 실험의 계좌 구간을 사용해야 합니다")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("비교 원자료 해시·커버리지가 다릅니다")
        baselines = sorted(
            (
                reference
                / ("seed-17/continuous" if cooldown_hours else "control/seed-17/continuous")
            ).glob("*/base.breakout-v1.json")
        )
        if not baselines:
            raise ValueError("대조 계좌 구간이 없습니다")
        parts: dict[str, list[Evaluation]] = {
            n: [] for n in ("breakout-v1", "cash", "prior", *models)
        }
        intervals = []
        for path in baselines:
            result = read(path)
            start, end = (datetime.fromisoformat(result[k]) for k in ("start", "end"))
            matching = [
                b
                for b in blocks
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matching) != 1:
                raise ValueError("계좌 구간에 연속 원자료가 없습니다")
            bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
            approvals = {b.close_time: 2 for b in bars if b.close_time >= start}
            for values in parts.values():
                values.append(Evaluation(start, bars, approvals))
            intervals.append({"start": start.isoformat(), "end": end.isoformat()})
        write_json(output / "intervals.json", intervals)
        group = output / "seed-17"
        group.mkdir()
        evaluate(
            parts,
            group,
            models=models,
            exit_lookbacks={n: 24 for n in models},
            cooldowns={candidate: cooldown_hours},
        )
        generated = sorted((group / "continuous").glob("*/base.breakout-v1.json"))
        if len(generated) != len(baselines):
            raise ValueError("재현 계좌 구간 수가 다릅니다")
        for old, new in zip(baselines, generated, strict=True):
            for scenario, _, _, _ in SCENARIOS:
                for name in ("breakout-v1", CANDIDATE) if cooldown_hours else ("breakout-v1",):
                    filename = f"{scenario}.{name}.json"
                    if read(old.parent / filename) != read(new.parent / filename):
                        raise ValueError("기존 대조 결과 재현 실패")
        summary = summarize(output, models=models, seeds=(17,))
        if cooldown_hours:
            from decimal import Decimal

            for row in summary["historical"]:
                if row["candidate"] == candidate:
                    differences = []
                    for path in generated:
                        prefix = row["scenario"]
                        current = read(path.parent / f"{prefix}.{candidate}.json")
                        previous = read(path.parent / f"{prefix}.{CANDIDATE}.json")
                        differences.append(
                            Decimal(current["net_return"]) - Decimal(previous["net_return"])
                        )
                    delta = median(differences)
                    row["delta_exit24"] = str(delta)
                    row["gate"] = row["gate"] and delta > 0
                    summary["exploratory_gate"][candidate] &= row["gate"]
        write_json(output / "results.json", summary)
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "baseline_parity": True,
                "exploratory_gate": summary["exploratory_gate"],
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="돌파 청산 채널 단축 오프라인 비교")
    for name in ("raw", "reference", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--cooldown-hours", type=int, choices=(0, 24), default=0)
    args = parser.parse_args()
    run_study(args.raw, args.reference, args.output, cooldown_hours=args.cooldown_hours)


if __name__ == "__main__":
    main()
