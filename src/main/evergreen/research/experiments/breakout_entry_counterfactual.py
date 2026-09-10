"""Experiment 61: matched-entry diagnostics, not an executable hindsight strategy."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from evergreen.market import Candle, load_dataset, write_json
from evergreen.research.backtest import (
    Costs,
    Fill,
    Result,
    _entry_path_context,
    _entry_trend_context,
    run_backtest,
)
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.report import source_identity

MODELS = (
    "breakout-v1",
    "cash",
    "prior",
    "entry-stop-budget",
    "entry-stop-trend-context",
    "liquidation-buffer",
    "liquidation-buffer-long",
    "entry-context",
    "entry-path-context",
)
LIMIT = Decimal(".1")
TOLERANCE = Decimal(".000001")


def simulate(
    bars: list[Candle], start: datetime, pricing: Costs, delay: int, entry: datetime | None = None
) -> Result:
    return run_backtest(
        bars,
        start,
        Decimal(1000000),
        pricing,
        "regime-mlp-v1",
        regimes={b.close_time: 2 for b in bars if b.close_time >= start},
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
        breakout_entry_stop=True,
        breakout_entry_stop_confirmations=2,
        breakout_entry_stop_channel_reset=True,
        breakout_entry_stop_profit_trail=True,
        breakout_entry_stop_adaptive_trail=True,
        breakout_entry_stop_trend_confirmation=True,
        breakout_entry_stop_budget=True,
        breakout_liquidation_buffer=True,
        long_entry_at=entry,
    )


def entry_features(bars: list[Candle], signal: datetime) -> dict[str, Any]:
    history = [b for b in bars if b.close_time <= signal]
    if len(history) < 26 or history[-1].close_time != signal:
        raise ValueError("진입 신호의 확정 봉이 누락됐습니다")
    advance = history[-1].close - history[-25].close
    travel = sum(
        abs(b.close - a.close) for a, b in zip(history[-25:-1], history[-24:], strict=True)
    )
    return {
        "signal_time": signal.isoformat(),
        "advance": str(advance),
        "prior24_tr": str(prior_mean_true_range(history)),
        "path_travel": str(travel),
        "rule59": _entry_trend_context(history),
        "rule60": _entry_path_context(history),
    }


def pair_record(original: Result, changed: Result, entry: Fill) -> dict[str, Any]:
    def position(result: Result) -> tuple[dict[str, Any], Fill]:
        matches = [i for i, f in enumerate(result.fills) if f == entry]
        if entry.side != "buy" or len(matches) != 1:
            raise ValueError("동일 실제 진입 체결이 없습니다")
        i = matches[0]
        if i + 1 >= len(result.fills):
            raise ValueError("진입 포지션의 청산 체결이 없습니다")
        sell = result.fills[i + 1]
        if (
            sell.side != "sell"
            or sell.quantity != entry.quantity
            or sell.btc_after != 0
            or sell.cash_after != entry.cash_after + (sell.price * sell.quantity - sell.fee)
        ):
            raise ValueError("진입 포지션의 전량 청산 정합성 오류")
        curve = [p for p in result.equity_curve if p.time <= entry.time]
        prefix = {
            "initial_capital": str(result.initial_capital),
            "start": result.start.isoformat(),
            "end": result.end.isoformat(),
            "costs": result.costs.model_dump(mode="json"),
            "delay": result.extra_delay_bars,
            "strategy": result.strategy,
            "fills": [f.model_dump(mode="json") for f in result.fills[: i + 1]],
            "curve": [p.model_dump(mode="json") for p in curve],
            "rejections": [
                r.model_dump(mode="json") for r in result.rejections if r.time <= entry.time
            ],
            "cash": str(entry.cash_after),
            "btc": str(entry.btc_after),
            "peak": str(max([result.initial_capital, *[p.equity for p in curve]])),
        }
        return prefix, sell

    a, exit_a = position(original)
    b, exit_b = position(changed)
    if a != b:
        raise ValueError("진입 이전 계좌 경로가 다릅니다")

    def outcome(result: Result, sell: Fill) -> dict[str, Any]:
        points = [p for p in result.equity_curve if entry.time <= p.time <= sell.time]
        return {
            "exit": sell.model_dump(mode="json"),
            "net_pnl": str(
                sell.price * sell.quantity - sell.fee - entry.price * entry.quantity - entry.fee
            ),
            "account_drawdown_during_position": str(max(p.drawdown for p in points)),
            "halted_by_exit": any(
                p.drawdown >= LIMIT for p in result.equity_curve if p.time <= sell.time
            ),
        }

    short, long = outcome(original, exit_a), outcome(changed, exit_b)
    delta = Decimal(long["net_pnl"]) - Decimal(short["net_pnl"])
    censored = exit_a.reason == "settlement" or exit_b.reason == "settlement"
    return {
        "entry": entry.model_dump(mode="json"),
        "prefix_equal": True,
        "prefix_sha256": hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest(),
        "entry_state": {k: a[k] for k in ("cash", "btc", "peak")},
        "short": short,
        "long": long,
        "status": "censored" if censored else "completed",
        "label": None
        if censored
        else "tie"
        if abs(delta) <= TOLERANCE
        else "long"
        if delta > 0
        else "short",
        "pnl_delta": None if censored else str(delta),
        "label_time": None if censored else max(exit_a.time, exit_b.time).isoformat(),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {}
    for scenario, _, _, _ in SCENARIOS:
        rows = [r for r in records if r["scenario"] == scenario]
        completed = [r for r in rows if r["status"] == "completed"]
        choices = {}
        for rule in ("rule59", "rule60"):
            choices[rule] = dict(
                Counter(
                    "tie"
                    if r["label"] == "tie"
                    else "correct"
                    if (r["features"][rule] == 168) == (r["label"] == "long")
                    else "missed_long"
                    if r["label"] == "long"
                    else "unnecessary_long"
                    for r in completed
                )
            )
        groups[scenario] = {
            "records": len(rows),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "labels": dict(Counter(r["label"] for r in completed)),
            "rules": choices,
            "risk_pairs": dict(
                Counter(
                    f"{r['short']['halted_by_exit']}/{r['long']['halted_by_exit']}"
                    for r in completed
                )
            ),
        }
    return {
        "scenarios": groups,
        "unique_signals": len({r["entry"]["signal_time"] for r in records}),
        "model_trained": False,
        "profitability_gate_passed": False,
        "live_enabled": False,
    }


def analyze(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        content = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    write_json(
        output / "protocol.json",
        {
            "experiment": "61",
            "prespecified_in": "docs/breakout-entry-path-context-60.md",
            "source": str(source.resolve()),
            "reference_experiment": "60",
            "baseline": "liquidation-buffer",
            "evaluation": "previously_observed_matched_entry_diagnostic_not_tradable_oracle",
            "label_tolerance": str(TOLERANCE),
            "model_trained": False,
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        if (
            read(source / "protocol.json")["experiment"] != "60"
            or read(source / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험60이 필요합니다")
        read(source / "datasets/coverage.json")
        intervals = read(source / "intervals.json")
        if not intervals:
            raise ValueError("계좌 구간이 없습니다")
        datasets = []
        for directory in sorted((source / "datasets").glob("block-*")):
            for name in ("candles.jsonl", "quality.json"):
                p = directory / name
                hashes[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
            datasets.append(load_dataset(directory)[0])
        records = []
        checks = 0
        parity = 0
        for i, interval in enumerate(intervals):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            matching = [
                b
                for b in datasets
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matching) != 1:
                raise ValueError("계좌 연속 자료가 없습니다")
            bars = [b for b in matching[0] if start - timedelta(hours=200) <= b.open_time < end]
            block = f"block-{i:03d}"
            for scenario, fee, slip, delay in SCENARIOS:
                directory = source / "seed-17/continuous" / block
                for model in MODELS:
                    payload = read(directory / f"{scenario}.{model}.json")
                    trade_pnl(payload)
                    checks += 1
                    if (
                        datetime.fromisoformat(payload["start"]) != start
                        or datetime.fromisoformat(payload["end"]) != end
                    ):
                        raise ValueError("대조 계좌 경계 불일치")
                expected = read(directory / f"{scenario}.liquidation-buffer.json")
                original = simulate(bars, start, costs(fee, slip), delay)
                if original.model_dump(mode="json") != expected:
                    raise ValueError("57 대조 전체 결과 불일치")
                parity += 1
                for j, entry in enumerate(original.fills[::2]):
                    if entry.signal_time is None:
                        raise ValueError("원래 진입 신호가 없습니다")
                    changed = simulate(bars, start, costs(fee, slip), delay, entry.signal_time)
                    trade_pnl(changed.model_dump(mode="json"))
                    record = pair_record(original, changed, entry)
                    features = entry_features(bars, entry.signal_time)
                    folder = output / "counterfactuals" / block
                    folder.mkdir(parents=True, exist_ok=True)
                    filename = folder / f"{scenario}.entry-{j:03d}.json"
                    write_json(filename, changed.model_dump(mode="json"))
                    hashes[str(filename.resolve())] = hashlib.sha256(
                        filename.read_bytes()
                    ).hexdigest()
                    records.append(
                        {
                            "block": block,
                            "scenario": scenario,
                            "features": features,
                            "counterfactual": str(filename.relative_to(output)),
                            **record,
                        }
                    )
            print(f"{block} 완료: 누적 진입 비교 {len(records)}개", flush=True)
        summary = summarize(records)
        write_json(output / "records.json", records)
        write_json(output / "results.json", summary)
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "source_pnl_checks": checks,
                "baseline_comparisons": parity,
                "entry_comparisons": len(records),
                "live_enabled": False,
            },
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="실험61 동일 진입 보유 선택 진단 · 실계좌 접근 없음"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.source, args.output)
