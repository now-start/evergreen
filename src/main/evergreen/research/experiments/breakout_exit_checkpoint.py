"""Experiments 53-55/57-60: offline exit guards with frozen controls."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from evergreen.market import write_json
from evergreen.research.experiments.breakout_meta import costs, evaluate, summarize
from evergreen.research.experiments.regime import SCENARIOS, Evaluation
from evergreen.research.experiments.regime_data import contiguous_blocks
from evergreen.research.report import source_identity

CANDIDATE = "exit-checkpoint-24h"
MODELS = ("entry-stop-budget", "entry-stop-trend-context", CANDIDATE)


def run_study(
    raw: Path,
    reference: Path,
    output: Path,
    *,
    higher_low: bool = False,
    extension_floor: bool = False,
    liquidation_buffer: bool = False,
    liquidation_buffer_long: bool = False,
    entry_context: bool = False,
    entry_path_context: bool = False,
) -> None:
    if (
        sum(
            (
                higher_low,
                extension_floor,
                liquidation_buffer,
                liquidation_buffer_long,
                entry_context,
                entry_path_context,
            )
        )
        > 1
    ):
        raise ValueError("연장 조건을 동시에 적용할 수 없습니다")
    buffered = liquidation_buffer or liquidation_buffer_long or entry_context or entry_path_context
    checkpoint = not (higher_low or extension_floor or buffered)
    controls = (
        (*MODELS[:2], "liquidation-buffer", "liquidation-buffer-long", "entry-context")
        if entry_path_context
        else (*MODELS[:2], "liquidation-buffer", "liquidation-buffer-long")
        if entry_context
        else (*MODELS[:2], "liquidation-buffer")
        if liquidation_buffer_long
        else MODELS[:2]
    )
    reference_experiment = (
        "59"
        if entry_path_context
        else "58"
        if entry_context
        else "57"
        if liquidation_buffer_long
        else "51"
    )
    candidate = (
        "entry-path-context"
        if entry_path_context
        else "entry-context"
        if entry_context
        else "liquidation-buffer-long"
        if liquidation_buffer_long
        else "liquidation-buffer"
        if liquidation_buffer
        else "extension-floor-2h"
        if extension_floor
        else "exit-higher-low"
        if higher_low
        else CANDIDATE
    )
    models = (*controls, candidate)
    experiment = (
        "60"
        if entry_path_context
        else "59"
        if entry_context
        else "58"
        if liquidation_buffer_long
        else "57"
        if liquidation_buffer
        else "55"
        if extension_floor
        else "54"
        if higher_low
        else "53"
    )
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": experiment,
            "reference_experiment": reference_experiment,
            "candidate": candidate,
            "prespecified_in": "docs/breakout-entry-context-59.md"
            if entry_path_context
            else "docs/breakout-liquidation-buffer-long-58.md"
            if entry_context
            else "docs/breakout-liquidation-buffer-57.md"
            if liquidation_buffer_long
            else "docs/breakout-risk-attribution-56.md"
            if liquidation_buffer
            else "docs/breakout-exit-higher-low-54.md"
            if extension_floor
            else "docs/breakout-exit-checkpoint-53.md"
            if higher_low
            else "docs/breakout-exit-extension-data-52.md",
            "checkpoint_hours": 24 if checkpoint else None,
            "checkpoint": "close_le_original_policy50_decision_close" if checkpoint else None,
            "liquidation_buffer": "net_sale_at_close_minus_(1+delay)*prior24_tr_le_90pct_peak"
            if buffered
            else None,
            "entry_context": "signal_close_minus_close_s-24_gt_3_prior24_tr_choose168_else24_frozen_at_fill"
            if entry_context
            else None,
            "entry_path_context": "signal_2_net_advance_gt_24_close_travel_choose168_else24_frozen_at_fill"
            if entry_path_context
            else None,
            "extension_floor": "two_consecutive_closes_below_frozen_decision_close"
            if extension_floor
            else None,
            "extension_permission": None
            if buffered
            else "prior24_low_gt_preceding24_low_excluding_current"
            if higher_low
            else "long_context_disagreement",
            "branch": "signal_time_context_preemptive_liquidation_no_extension"
            if entry_context or entry_path_context
            else "policy51_preemptive_liquidation_no_extension"
            if liquidation_buffer_long
            else "policy50_preemptive_liquidation_no_extension"
            if liquidation_buffer
            else "current_position_only_long_context_when_short_and_long_disagree",
            "entry_lookback": 168,
            "entry_buffer": ".001",
            "exit_lookback": 48,
            "warmup": 200,
            "capital": "1000000",
            "base_costs": costs().model_dump(mode="json"),
            "trained_models": 0,
            "live_enabled": False,
            "evaluation": "previously_observed_exploration_not_fresh_holdout",
            **source_identity(),
        },
    )
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        content = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    try:
        if (
            read(reference / "protocol.json")["experiment"] != reference_experiment
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError(f"완료된 실험{reference_experiment} 대조가 필요합니다")
        blocks = contiguous_blocks(raw, output / "datasets")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("원자료 해시·커버리지가 다릅니다")
        intervals = read(reference / "intervals.json")
        paths = sorted((reference / "seed-17/continuous").glob("*/base.breakout-v1.json"))
        if not intervals or len(paths) != len(intervals):
            raise ValueError("대조 계좌 구간이 누락됐습니다")
        parts: dict[str, list[Evaluation]] = {
            name: [] for name in ("breakout-v1", "cash", "prior", *models)
        }
        for interval, path in zip(intervals, paths, strict=True):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            old = read(path)
            if any(
                datetime.fromisoformat(old[k]) != value
                for k, value in (("start", start), ("end", end))
            ):
                raise ValueError("대조 계좌 경계가 다릅니다")
            matching = [
                b
                for b in blocks
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matching) != 1:
                raise ValueError("계좌 구간의 연속 원자료가 없습니다")
            bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
            approvals = {b.close_time: 2 for b in bars if b.close_time >= start}
            for name in parts:
                parts[name].append(Evaluation(start, bars, approvals))
        write_json(output / "intervals.json", intervals)
        group = output / "seed-17"
        evaluate(
            parts,
            group,
            models=models,
            entry_stop_models=models,
            entry_stop_confirmations=dict.fromkeys(models, 2),
            entry_stop_channel_reset_models=models,
            entry_stop_profit_trail_models=models,
            entry_stop_adaptive_trail_models=models,
            entry_stop_trend_confirmation_models=models,
            entry_stop_budget_models=models,
            entry_stop_trend_lookbacks={
                "entry-stop-trend-context": 168,
                **({"liquidation-buffer-long": 168} if entry_context or entry_path_context else {}),
                **({candidate: 168} if liquidation_buffer_long else {}),
            },
            exit_checkpoint_models=(candidate,) if checkpoint else (),
            exit_higher_low_models=(candidate,) if higher_low else (),
            extension_floor_models=(candidate,) if extension_floor else (),
            entry_context_models=("entry-context",)
            if entry_path_context
            else (candidate,)
            if entry_context
            else (),
            entry_path_context_models=(candidate,) if entry_path_context else (),
            liquidation_buffer_models=(
                "liquidation-buffer",
                "liquidation-buffer-long",
                "entry-context",
                candidate,
            )
            if entry_path_context
            else ("liquidation-buffer", "liquidation-buffer-long", candidate)
            if entry_context
            else ("liquidation-buffer", candidate)
            if liquidation_buffer_long
            else (candidate,)
            if liquidation_buffer
            else (),
        )
        generated = sorted((group / "continuous").glob("*/base.breakout-v1.json"))
        if len(generated) != len(paths):
            raise ValueError("재현 계좌 구간 수가 다릅니다")
        comparisons = 0
        for old, new in zip(paths, generated, strict=True):
            for scenario, _, _, _ in SCENARIOS:
                for model in ("breakout-v1", *controls):
                    filename = f"{scenario}.{model}.json"
                    if read(old.parent / filename) != read(new.parent / filename):
                        raise ValueError(f"대조 {model} 재현 실패")
                    comparisons += 1
        summary = summarize(output, models=models, seeds=(17,))
        write_json(output / "results.json", summary)
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "baseline_parity": True,
                "control_comparisons": comparisons,
                "exploratory_gate": summary["exploratory_gate"],
                "live_enabled": False,
            },
        )
        print(
            f"실험{experiment} 완료: 대조 {comparisons}개 일치, {summary['exploratory_gate']}",
            flush=True,
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험53 청산 연장 중간 점검 · 실계좌 접근 없음")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--higher-low", action="store_true", help="실험54 저점 상승 연장 허가")
    modes.add_argument(
        "--extension-floor", action="store_true", help="실험55 연장 가격 이탈 두 종가"
    )
    modes.add_argument(
        "--liquidation-buffer", action="store_true", help="실험57 청산 가능 자산 여유"
    )
    modes.add_argument(
        "--liquidation-buffer-long", action="store_true", help="실험58 긴 추세와 청산 여유"
    )
    modes.add_argument("--entry-context", action="store_true", help="실험59 진입 신호로 추세 고정")
    modes.add_argument(
        "--entry-path-context", action="store_true", help="실험60 진입 경로로 추세 고정"
    )
    for name in ("raw", "reference", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    run_study(
        args.raw,
        args.reference,
        args.output,
        higher_low=args.higher_low,
        extension_floor=args.extension_floor,
        liquidation_buffer=args.liquidation_buffer,
        liquidation_buffer_long=args.liquidation_buffer_long,
        entry_context=args.entry_context,
        entry_path_context=args.entry_path_context,
    )
