"""Experiment 62: frozen standalone strategy families on matched offline accounts."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from statistics import median
from typing import Any, cast

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Costs, Result, run_backtest
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.regime_data import contiguous_blocks
from evergreen.research.report import source_identity
from evergreen.strategies import STRATEGY_PARAMETERS, Strategy

CONTROLS = ("breakout-v1", "cash", "entry-stop-budget", "liquidation-buffer")
CANDIDATES = ("rsi-rebound-v1", "band-reversion-v1")
MODELS = CONTROLS + CANDIDATES


def simulate(
    bars: list[Candle],
    start: datetime,
    name: str,
    pricing: Costs,
    delay: int,
    *,
    entry_lookback: int = 168,
    entry_trend_filter: bool = False,
    entry_slope_filter: bool = False,
    entry_cadence_hours: int = 1,
) -> Result:
    if name not in MODELS:
        raise ValueError("알 수 없는 전략입니다")
    if (
        entry_lookback != 168
        or entry_trend_filter
        or entry_slope_filter
        or entry_cadence_hours != 1
    ) and name != "liquidation-buffer":
        raise ValueError("진입 창 변경은 청산 버퍼 후보에만 적용합니다")
    if name in ("entry-stop-budget", "liquidation-buffer"):
        return run_backtest(
            bars,
            start,
            D(1000000),
            pricing,
            "regime-mlp-v1",
            extra_delay_bars=delay,
            regimes={b.close_time: 2 for b in bars if b.close_time >= start},
            regime_policy="breakout-filter",
            breakout_entry_stop=True,
            breakout_entry_stop_confirmations=2,
            breakout_entry_stop_channel_reset=True,
            breakout_entry_stop_profit_trail=True,
            breakout_entry_stop_adaptive_trail=True,
            breakout_entry_stop_trend_confirmation=True,
            breakout_entry_stop_budget=True,
            breakout_liquidation_buffer=name == "liquidation-buffer",
            breakout_entry_lookback=entry_lookback,
            breakout_entry_trend_filter=entry_trend_filter,
            breakout_entry_slope_filter=entry_slope_filter,
            breakout_entry_cadence_hours=entry_cadence_hours,
        )
    return run_backtest(
        bars, start, D(1000000), pricing, cast(Strategy, name), extra_delay_bars=delay
    )


def summarize(results: list[Result], baseline: list[Result], scenario: str) -> dict[str, Any]:
    if not results or len(results) != len(baseline):
        raise ValueError("대응 계좌 수가 다릅니다")
    differences = []
    for result, control in zip(results, baseline, strict=True):
        if (
            result.start,
            result.end,
            result.initial_capital,
            result.costs,
            result.extra_delay_bars,
        ) != (
            control.start,
            control.end,
            control.initial_capital,
            control.costs,
            control.extra_delay_bars,
        ):
            raise ValueError("대응 계좌 조건이 다릅니다")
        differences.append(result.net_return - control.net_return)
    paired = median(differences)
    positive = sum(r.net_return > 0 for r in results)
    drawdown = max(r.max_drawdown for r in results)
    halted = sum(r.halted for r in results)
    exits = sum(r.natural_exits for r in results)
    return {
        "accounts": len(results),
        "median_return": str(median(r.net_return for r in results)),
        "paired_median_vs_raw": str(paired),
        "positive_accounts": positive,
        "max_drawdown": str(drawdown),
        "halted_accounts": halted,
        "natural_exits": exits,
        "total_pnl": str(sum((r.final_equity - r.initial_capital for r in results), D(0))),
        "total_fees": str(sum((r.total_fees for r in results), D(0))),
        "total_slippage": str(sum((r.total_slippage for r in results), D(0))),
        "gate": paired > 0
        and positive > len(results) / 2
        and drawdown <= D(".1")
        and halted == 0
        and (scenario != "base" or exits >= 10),
    }


def run_study(raw: Path, reference: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "62",
            "reference_experiment": "60",
            "prespecified_in": "docs/breakout-entry-counterfactual-61.md",
            "candidates": {n: STRATEGY_PARAMETERS[cast(Strategy, n)] for n in CANDIDATES},
            "controls": CONTROLS,
            "warmup": 200,
            "capital": "1000000",
            "base_costs": costs().model_dump(mode="json"),
            "scenarios": SCENARIOS,
            "trained_models": 0,
            "live_enabled": False,
            "evaluation": "previously_observed_continuous_accounts_not_unseen_holdout",
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
            read(reference / "protocol.json")["experiment"] != "60"
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험60 대조가 필요합니다")
        blocks = contiguous_blocks(raw, output / "datasets")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("원자료 해시·커버리지가 다릅니다")
        intervals = read(reference / "intervals.json")
        paths = sorted((reference / "seed-17/continuous").glob("*/base.breakout-v1.json"))
        if not intervals or len(paths) != len(intervals):
            raise ValueError("대조 계좌 구간이 누락됐습니다")
        write_json(output / "intervals.json", intervals)
        parts: dict[str, dict[str, list[Result]]] = {
            scenario: {name: [] for name in MODELS} for scenario, *_ in SCENARIOS
        }
        comparisons = pnl_checks = 0
        for interval, path in zip(intervals, paths, strict=True):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            matching = [
                b
                for b in blocks
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matching) != 1:
                raise ValueError("계좌 구간의 연속 원자료가 없습니다")
            bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
            directory = output / "seed-17/continuous" / path.parent.name
            directory.mkdir(parents=True)
            for scenario, fee, slip, delay in SCENARIOS:
                for name in MODELS:
                    result = simulate(bars, start, name, costs(fee, slip), delay)
                    if result.start != start or result.end != end:
                        raise ValueError("대조 계좌 경계가 다릅니다")
                    data = result.model_dump(mode="json")
                    trade_pnl(data)
                    pnl_checks += 1
                    filename = f"{scenario}.{name}.json"
                    write_json(directory / filename, data)
                    read(directory / filename)
                    if name in CONTROLS:
                        if data != read(path.parent / filename):
                            raise ValueError(f"대조 {name} 재현 실패")
                        comparisons += 1
                    parts[scenario][name].append(result)
            print(f"실험62 {path.parent.name} 완료", flush=True)
        summaries = {
            scenario: {
                name: summarize(rows, group["breakout-v1"], scenario)
                for name, rows in group.items()
            }
            for scenario, group in parts.items()
        }
        gates = {
            name: all(group[name]["gate"] for group in summaries.values()) for name in CANDIDATES
        }
        write_json(output / "results.json", {"scenarios": summaries, "exploratory_gate": gates})
        for artifact in (output / "datasets").rglob("*.json*"):
            hashes[str(artifact.resolve())] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "control_comparisons": comparisons,
                "pnl_checks": pnl_checks,
                "exploratory_gate": gates,
                "live_enabled": False,
            },
        )
        print(f"실험62 완료: {gates}", flush=True)
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="실험62 기존 RSI·밴드 장기 비교 · 실계좌 접근 없음"
    )
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.raw, args.reference, args.output)
