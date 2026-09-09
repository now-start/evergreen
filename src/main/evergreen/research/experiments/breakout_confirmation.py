"""Experiments 26-40: independent filters or exits; original breakout entry required."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from evergreen.market import Candle, write_json
from evergreen.research.breakout_features import prior_mean_true_range
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


def extension_schedule(bars: list[Candle], start: datetime) -> dict[datetime, int]:
    return {
        b.close_time: 2
        if i >= 168
        and b.close - max(p.high for p in bars[i - 168 : i])
        <= prior_mean_true_range(bars[i - 25 : i + 1])
        else 0
        for i, b in enumerate(bars)
        if b.close_time >= start
    }


def expansion_schedule(bars: list[Candle], start: datetime) -> dict[datetime, int]:
    return {
        b.close_time: 2
        if i >= 169
        and prior_mean_true_range(bars[i - 25 : i + 1])
        > prior_mean_true_range(bars[i - 169 : i + 1], lookback=168)
        else 0
        for i, b in enumerate(bars)
        if b.close_time >= start
    }


def compression_schedule(bars: list[Candle], start: datetime) -> dict[datetime, int]:
    return {
        b.close_time: 2
        if i >= 169
        and prior_mean_true_range(bars[i - 25 : i + 1])
        <= prior_mean_true_range(bars[i - 169 : i + 1], lookback=168)
        else 0
        for i, b in enumerate(bars)
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
    failed_breakout_exit: bool = False,
    confirmed_failure_exit: bool = False,
    volatility_failure_exit: bool = False,
    extension_cap: bool = False,
    trailing_exit: bool = False,
    profit_trailing_exit: bool = False,
    adaptive_trailing_exit: bool = False,
    tr_expansion: bool = False,
    tr_compression: bool = False,
    rejection_latch: bool = False,
    entry_stop: bool = False,
) -> None:
    if (
        sum(
            (
                volume_confirmation,
                wick_confirmation,
                risk_budget,
                failed_breakout_exit,
                confirmed_failure_exit,
                volatility_failure_exit,
                extension_cap,
                trailing_exit,
                profit_trailing_exit,
                adaptive_trailing_exit,
                tr_expansion,
                tr_compression,
                rejection_latch,
                entry_stop,
            )
        )
        > 1
    ):
        raise ValueError("연구 조건을 동시에 적용할 수 없습니다")
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
    if failed_breakout_exit:
        candidate, experiment, reference_experiment = "failed-breakout", "30", "29"
        confirmation = "close_below_frozen_entry_signal_prior_168_high"
    if confirmed_failure_exit:
        candidate, experiment, reference_experiment = "failed-breakout-2h", "31", "30"
        confirmation = "two_holding_closes_below_frozen_entry_signal_prior_168_high"
    models: tuple[str, ...] = (
        ("failed-breakout", candidate) if confirmed_failure_exit else (candidate,)
    )
    if volatility_failure_exit:
        candidate, experiment, reference_experiment = "failed-breakout-tr24", "32", "31"
        confirmation = "close_below_frozen_entry_ceiling_minus_prior24_mean_true_range"
        models = ("failed-breakout", "failed-breakout-2h", candidate)
    failure_mode = failed_breakout_exit or confirmed_failure_exit or volatility_failure_exit
    failure_models = models if failure_mode else ()
    buffer_models = (candidate,) if volatility_failure_exit else ()
    if extension_cap:
        candidate, experiment, reference_experiment = "entry-cap-tr24", "33", "32"
        confirmation = "close_minus_prior168_ceiling_le_prior24_mean_true_range"
        schedule = extension_schedule
        failure_models = ("failed-breakout", "failed-breakout-2h", "failed-breakout-tr24")
        buffer_models = ("failed-breakout-tr24",)
        models = (*failure_models, candidate)
    if trailing_exit:
        candidate, experiment, reference_experiment = "trailing-tr24", "34", "33"
        confirmation = "close_below_held_close_peak_minus_3_frozen_entry_prior24_mean_true_range"
        models = ("entry-cap-tr24", candidate)
    if profit_trailing_exit:
        candidate, experiment, reference_experiment = "profit-trailing-tr24", "35", "34"
        confirmation = "trailing_tr24_after_held_peak_ge_actual_entry_open_plus_trailing_distance"
        models = ("trailing-tr24", candidate)
    if adaptive_trailing_exit:
        candidate, experiment, reference_experiment = "adaptive-trailing-tr24", "36", "35"
        confirmation = "profit_trailing_with_3_max_entry_and_current_prior24_mean_true_range"
        models = ("profit-trailing-tr24", candidate)
    profit_trailing_mode = profit_trailing_exit or adaptive_trailing_exit
    trailing_models = models if profit_trailing_mode else (candidate,) if trailing_exit else ()
    profit_models = (
        models if adaptive_trailing_exit else (candidate,) if profit_trailing_exit else ()
    )
    adaptive_models = (candidate,) if adaptive_trailing_exit else ()
    if tr_expansion:
        candidate, experiment, reference_experiment = "tr-expansion-24-168", "37", "36"
        confirmation, schedule = (
            "prior24_mean_true_range_gt_prior168_mean_true_range",
            expansion_schedule,
        )
        models = ("adaptive-trailing-tr24", candidate)
        trailing_models = profit_models = adaptive_models = ("adaptive-trailing-tr24",)
    if tr_compression:
        candidate, experiment, reference_experiment = "tr-compression-24-168", "38", "37"
        confirmation, schedule = (
            "prior24_mean_true_range_le_prior168_mean_true_range",
            compression_schedule,
        )
        models = ("tr-expansion-24-168", candidate)
    if rejection_latch:
        candidate, experiment, reference_experiment = "expansion-rejection-latch", "39", "38"
        confirmation, schedule = (
            "expansion_rejection_until_close_le_frozen_prior168_ceiling",
            expansion_schedule,
        )
        models = ("tr-expansion-24-168", "tr-compression-24-168", candidate)
    latch_models = (candidate,) if rejection_latch else ()
    if entry_stop:
        candidate, experiment, reference_experiment = "entry-stop-tr24", "40", "39"
        confirmation = "close_below_actual_entry_open_minus_3_frozen_entry_prior24_mean_true_range"
        models = ("expansion-rejection-latch", candidate)
        latch_models = ("expansion-rejection-latch",)
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
            n: [] for n in ("breakout-v1", "cash", "prior", *models)
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
                or failure_mode
                or trailing_exit
                or profit_trailing_mode
                or entry_stop
                else schedule(bars, start)
            )
            model_approvals = {candidate: approvals}
            if trailing_exit:
                model_approvals["entry-cap-tr24"] = extension_schedule(bars, start)
            if tr_compression or rejection_latch:
                model_approvals["tr-expansion-24-168"] = expansion_schedule(bars, start)
            if rejection_latch:
                model_approvals["tr-compression-24-168"] = compression_schedule(bars, start)
            if entry_stop:
                model_approvals["expansion-rejection-latch"] = expansion_schedule(bars, start)
            for name in parts:
                parts[name].append(
                    Evaluation(start, bars, model_approvals.get(name, dict.fromkeys(approvals, 2)))
                )
        write_json(output / "intervals.json", intervals)
        group = output / "seed-17"
        group.mkdir()
        evaluate(
            parts,
            group,
            models=models,
            risk_budget_models=(candidate,) if risk_budget else (),
            failure_exit_models=failure_models,
            failure_exit_confirmations={"failed-breakout-2h": 2}
            if confirmed_failure_exit or volatility_failure_exit or extension_cap
            else None,
            failure_exit_buffer_models=buffer_models,
            trailing_exit_models=trailing_models,
            trailing_profit_only_models=profit_models,
            trailing_adaptive_models=adaptive_models,
            rejection_latch_models=latch_models,
            entry_stop_models=(candidate,) if entry_stop else (),
        )
        generated = sorted((group / "continuous").glob("*/base.breakout-v1.json"))
        if len(generated) != len(baselines):
            raise ValueError("재현 계좌 구간 수가 다릅니다")
        for old, new in zip(baselines, generated, strict=True):
            for scenario, _, _, _ in SCENARIOS:
                controls: tuple[str, ...] = (
                    ("breakout-v1", "failed-breakout")
                    if confirmed_failure_exit
                    else ("breakout-v1",)
                )
                if volatility_failure_exit:
                    controls = ("breakout-v1", "failed-breakout", "failed-breakout-2h")
                if extension_cap:
                    controls = ("breakout-v1", *failure_models)
                if trailing_exit:
                    controls = ("breakout-v1", "entry-cap-tr24")
                if profit_trailing_exit:
                    controls = ("breakout-v1", "trailing-tr24")
                if adaptive_trailing_exit:
                    controls = ("breakout-v1", "profit-trailing-tr24")
                if tr_expansion:
                    controls = ("breakout-v1", "adaptive-trailing-tr24")
                if tr_compression:
                    controls = ("breakout-v1", "tr-expansion-24-168")
                if rejection_latch:
                    controls = ("breakout-v1", "tr-expansion-24-168", "tr-compression-24-168")
                if entry_stop:
                    controls = ("breakout-v1", "expansion-rejection-latch")
                for control in controls:
                    filename = f"{scenario}.{control}.json"
                    if read(old.parent / filename) != read(new.parent / filename):
                        raise ValueError(f"대조 {control} 재현 실패")
        summary = summarize(output, models=models, seeds=(17,))
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
    parser = argparse.ArgumentParser(description="돌파 단일 진입 필터·청산 조건 비교")
    filters = parser.add_mutually_exclusive_group()
    filters.add_argument("--volume-confirmation", action="store_true")
    filters.add_argument("--wick-confirmation", action="store_true")
    filters.add_argument("--risk-budget", action="store_true")
    filters.add_argument("--failed-breakout-exit", action="store_true")
    filters.add_argument("--confirmed-failure-exit", action="store_true")
    filters.add_argument("--volatility-failure-exit", action="store_true")
    filters.add_argument("--extension-cap", action="store_true")
    filters.add_argument("--trailing-exit", action="store_true")
    filters.add_argument("--profit-trailing-exit", action="store_true")
    filters.add_argument("--adaptive-trailing-exit", action="store_true")
    filters.add_argument("--tr-expansion", action="store_true")
    filters.add_argument("--tr-compression", action="store_true")
    filters.add_argument("--rejection-latch", action="store_true")
    filters.add_argument("--entry-stop", action="store_true")
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
        failed_breakout_exit=args.failed_breakout_exit,
        confirmed_failure_exit=args.confirmed_failure_exit,
        volatility_failure_exit=args.volatility_failure_exit,
        extension_cap=args.extension_cap,
        trailing_exit=args.trailing_exit,
        profit_trailing_exit=args.profit_trailing_exit,
        adaptive_trailing_exit=args.adaptive_trailing_exit,
        tr_expansion=args.tr_expansion,
        tr_compression=args.tr_compression,
        rejection_latch=args.rejection_latch,
        entry_stop=args.entry_stop,
    )


if __name__ == "__main__":
    main()
