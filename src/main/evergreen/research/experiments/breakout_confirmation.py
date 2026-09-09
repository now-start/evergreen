"""Experiments 26-29: independent entry filters; current breakout remains required."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from evergreen.market import Candle, write_json
from evergreen.research.experiments.breakout_meta import costs, evaluate, summarize
from evergreen.research.experiments.regime import SCENARIOS, Evaluation
from evergreen.research.experiments.regime_data import contiguous_blocks
from evergreen.research.report import source_identity
from evergreen.strategies.breakout import target

CANDIDATE = "confirm-1h"


def confirmation_schedule(bars: list[Candle], start: datetime) -> dict[datetime, int]:
    return {
        b.close_time: 2 if i >= 169 and target(bars[i - 169 : i], False) == "buy" else 0
        for i, b in enumerate(bars)
        if b.close_time >= start
    }


def volume_schedule(bars: list[Candle], start: datetime) -> dict[datetime, int]:
    return {
        b.close_time: 2
        if i >= 24 and b.volume > sum((p.volume for p in bars[i - 24 : i]), Decimal(0)) / 24
        else 0
        for i, b in enumerate(bars)
        if b.close_time >= start
    }


def wick_schedule(bars: list[Candle], start: datetime) -> dict[datetime, int]:
    return {
        b.close_time: 2 if 4 * (b.high - max(b.open, b.close)) <= b.high - b.low else 0
        for b in bars
        if b.close_time >= start
    }


def run_study(
    raw: Path,
    reference: Path,
    output: Path,
    *,
    volume_confirmation: bool = False,
    wick_confirmation: bool = False,
    risk_budget: bool = False,
) -> None:
    if sum((volume_confirmation, wick_confirmation, risk_budget)) > 1:
        raise ValueError("진입 필터를 동시에 적용할 수 없습니다")
    candidate = "volume-24h" if volume_confirmation else CANDIDATE
    experiment, reference_experiment = ("27", "26") if volume_confirmation else ("26", "25")
    confirmation = (
        "current_volume_gt_prior_24_mean"
        if volume_confirmation
        else "two_consecutive_independent_breakouts"
    )
    schedule = volume_schedule if volume_confirmation else confirmation_schedule
    if wick_confirmation:
        candidate, experiment, reference_experiment = "wick-25", "28", "27"
        confirmation, schedule = "current_upper_wick_le_25pct_range", wick_schedule
    if risk_budget:
        candidate, experiment, reference_experiment = "channel-budget", "29", "28"
        confirmation = "projected_channel_exit_cash_ge_90pct_current_peak"
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": experiment,
            "candidate": candidate,
            "confirmation": confirmation,
            "entry_lookback": 168,
            "entry_buffer": ".001",
            "exit_lookback": 48,
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
        if (
            read(reference / "protocol.json")["experiment"] != reference_experiment
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError(f"완료된 실험 {reference_experiment} 대조가 필요합니다")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("원자료 해시·커버리지가 다릅니다")
        intervals = read(reference / "intervals.json")
        baselines = sorted((reference / "seed-17/continuous").glob("*/base.breakout-v1.json"))
        if not intervals or len(baselines) != len(intervals):
            raise ValueError("대조 계좌 구간이 누락됐습니다")
        parts: dict[str, list[Evaluation]] = {
            n: [] for n in ("breakout-v1", "cash", "prior", candidate)
        }
        for interval, path in zip(intervals, baselines, strict=True):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            old = read(path)
            if any(
                datetime.fromisoformat(old[k]) != datetime.fromisoformat(interval[k])
                for k in ("start", "end")
            ):
                raise ValueError("대조 계좌 구간이 다릅니다")
            matching = [
                b
                for b in blocks
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matching) != 1:
                raise ValueError("계좌 구간의 연속 원자료가 없습니다")
            bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
            approvals = (
                dict.fromkeys((b.close_time for b in bars if b.close_time >= start), 2)
                if risk_budget
                else schedule(bars, start)
            )
            for name in parts:
                parts[name].append(
                    Evaluation(
                        start, bars, approvals if name == candidate else dict.fromkeys(approvals, 2)
                    )
                )
        write_json(output / "intervals.json", intervals)
        group = output / "seed-17"
        group.mkdir()
        evaluate(
            parts,
            group,
            models=(candidate,),
            risk_budget_models=(candidate,) if risk_budget else (),
        )
        generated = sorted((group / "continuous").glob("*/base.breakout-v1.json"))
        if len(generated) != len(baselines):
            raise ValueError("재현 계좌 구간 수가 다릅니다")
        for old, new in zip(baselines, generated, strict=True):
            for scenario, _, _, _ in SCENARIOS:
                filename = f"{scenario}.breakout-v1.json"
                if read(old.parent / filename) != read(new.parent / filename):
                    raise ValueError("원래 돌파 재현 실패")
        summary = summarize(output, models=(candidate,), seeds=(17,))
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
    parser = argparse.ArgumentParser(description="돌파 단일 진입 필터 비교")
    filters = parser.add_mutually_exclusive_group()
    filters.add_argument("--volume-confirmation", action="store_true")
    filters.add_argument("--wick-confirmation", action="store_true")
    filters.add_argument("--risk-budget", action="store_true")
    for name in ("raw", "reference", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    run_study(
        args.raw,
        args.reference,
        args.output,
        volume_confirmation=args.volume_confirmation,
        wick_confirmation=args.wick_confirmation,
        risk_budget=args.risk_budget,
    )


if __name__ == "__main__":
    main()
