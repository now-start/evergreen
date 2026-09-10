"""Experiment 69: diagnostic base/delayed signal paths and changed halt-state replays."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from functools import partial
from pathlib import Path
from typing import Any

from evergreen.market import load_dataset, write_json
from evergreen.research.backtest import Fill, Result
from evergreen.research.experiments.account_sensitivity import CADENCE, MODELS
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.breakout_risk_attribution import first_breach, observe
from evergreen.research.experiments.strategy_family import simulate
from evergreen.research.report import source_identity

SCENARIOS = (("base", 0), ("delay-1h", 1))


def signal_difference(base: Result, delayed: Result) -> dict[str, Any]:
    def key(fill: Fill) -> tuple[Any, ...]:
        return fill.side, fill.signal_time, fill.reason

    common = 0
    lags = []
    for a, b in zip(base.fills, delayed.fills, strict=False):
        if key(a) != key(b):
            break
        common += 1
        lags.append((b.time - a.time).total_seconds() / 3600)
    difference = None
    if common != len(base.fills) or common != len(delayed.fills):
        difference = {
            "index": common,
            "base": base.fills[common].model_dump(mode="json")
            if common < len(base.fills)
            else None,
            "delayed": delayed.fills[common].model_dump(mode="json")
            if common < len(delayed.fills)
            else None,
        }
    return {
        "common_signal_prefix": common,
        "first_signal_divergence": difference,
        "matched_prefix_execution_lags_hours": lags,
    }


def compare(base: Result, delayed: Result) -> dict[str, Any]:
    if (
        (base.start, base.end, base.initial_capital, base.costs, base.strategy)
        != (delayed.start, delayed.end, delayed.initial_capital, delayed.costs, delayed.strategy)
        or base.extra_delay_bars != 0
        or delayed.extra_delay_bars != 1
    ):
        raise ValueError("기본/지연의 대응 계좌 조건이 다릅니다")
    for r in (base, delayed):
        trade_pnl(r.model_dump(mode="json"))
        if (
            not r.net_return.is_finite()
            or r.net_return != (r.final_equity - r.initial_capital) / r.initial_capital
            or r.final_equity != r.cash
            or sum((f.fee for f in r.fills), D(0)) != r.total_fees
            or sum((f.slippage_cost for f in r.fills), D(0)) != r.total_slippage
        ):
            raise ValueError("수익률·체결 비용·평가금 불일치")
    return {
        "start": base.start.isoformat(),
        "end": base.end.isoformat(),
        "base_pnl": str(base.final_equity - base.initial_capital),
        "delayed_pnl": str(delayed.final_equity - delayed.initial_capital),
        "pnl_change": str(delayed.final_equity - base.final_equity),
        "base_halted": base.halted,
        "delayed_halted": delayed.halted,
        "base_max_drawdown": str(base.max_drawdown),
        "delayed_max_drawdown": str(delayed.max_drawdown),
        "halt_changed": base.halted != delayed.halted,
        "base_trades": len(base.fills) // 2,
        "delayed_trades": len(delayed.fills) // 2,
        "base_buy_rejections": [
            r.model_dump(mode="json") for r in base.rejections if r.side == "buy"
        ],
        "delayed_buy_rejections": [
            r.model_dump(mode="json") for r in delayed.rejections if r.side == "buy"
        ],
        **signal_difference(base, delayed),
    }


def relative_changes(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    rows = []
    for raw, buffer in zip(groups["breakout-v1"], groups["liquidation-buffer"], strict=True):
        if raw["account"] != buffer["account"]:
            raise ValueError("비교 계좌 순서가 다릅니다")
        rows.append(
            {
                "account": raw["account"],
                "relative_delay_change": str(D(buffer["pnl_change"]) - D(raw["pnl_change"])),
            }
        )
    return rows


def run_study(reference: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "69",
            "reference_experiment": "67",
            "prespecified_in": "docs/account-sensitivity-68.md",
            "models": MODELS,
            "scenarios": SCENARIOS,
            "trained_models": 0,
            "promotion_candidate": False,
            "live_enabled": False,
            "evaluation": "previously_observed_execution_path_diagnostic_not_causal_price_effect",
            "replay_rule": "both_scenarios_for_every_changed_halt_pair_full_json_parity",
            **source_identity(),
        },
    )
    hashes: dict[str, str] = {}

    def content(path: Path, expected: dict[str, str] | None = None) -> bytes:
        value = path.read_bytes()
        key, digest = str(path.resolve()), hashlib.sha256(value).hexdigest()
        if expected is not None and expected.get(key) != digest:
            raise ValueError(f"입력 해시 누락·불일치: {path.name}")
        hashes[key] = digest
        return value

    def read(path: Path, expected: dict[str, str] | None = None) -> Any:
        return json.loads(content(path, expected))

    try:
        if (
            read(reference / "protocol.json")["experiment"] != "67"
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험67 결과가 필요합니다")
        expected = read(reference / "inputs.json")
        intervals = read(reference / "intervals.json")
        directories = sorted((reference / "seed-17/continuous").glob("block-*"))
        if not intervals or len(intervals) != len(directories):
            raise ValueError("계좌 구간 누락·추가")
        datasets = []
        for directory in sorted((reference / "datasets").glob("block-*")):
            for name in ("candles.jsonl", "quality.json"):
                content(directory / name, expected)
            datasets.append(load_dataset(directory)[0])
        groups: dict[str, list[dict[str, Any]]] = {m: [] for m in MODELS}
        checked = replayed = 0
        previous_end = None
        for interval, directory in zip(intervals, directories, strict=True):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            if start >= end or (previous_end is not None and previous_end > start):
                raise ValueError("시간순 계좌 경계 오류")
            previous_end = end
            matches = [
                b
                for b in datasets
                if b[0].open_time <= start - timedelta(hours=200) and b[-1].close_time >= end
            ]
            if len(matches) != 1:
                raise ValueError("대응 연속 봉이 없습니다")
            bars = [b for b in matches[0] if start - timedelta(hours=200) <= b.open_time < end]
            for name in MODELS:
                originals = []
                for scenario, delay in SCENARIOS:
                    data = read(directory / f"{scenario}.{name}.json", expected)
                    r = Result.model_validate(data)
                    if (
                        r.start,
                        r.end,
                        r.initial_capital,
                        r.costs,
                        r.extra_delay_bars,
                        r.strategy,
                    ) != (
                        start,
                        end,
                        D(1000000),
                        costs(),
                        delay,
                        "breakout-v1" if name == "breakout-v1" else "regime-mlp-v1",
                    ):
                        raise ValueError("원 실험의 시간·원금·비용·전략 조건 불일치")
                    originals.append(r)
                    checked += 1
                row = {"account": directory.name, **compare(*originals)}
                if row["halt_changed"]:
                    traces = {}
                    for (scenario, delay), original in zip(SCENARIOS, originals, strict=True):
                        actual, marks = observe(
                            partial(
                                simulate,
                                bars,
                                start,
                                "liquidation-buffer" if name == CADENCE else name,
                                costs(),
                                delay,
                                entry_cadence_hours=4 if name == CADENCE else 1,
                            )
                        )
                        if actual.model_dump(mode="json") != original.model_dump(mode="json"):
                            raise ValueError(f"관찰 재현 실패: {directory.name}/{name}/{scenario}")
                        traces[scenario] = first_breach(actual, marks)
                        replayed += 1
                    row["risk_replay"] = traces
                groups[name].append(row)
            print(f"실험69 {directory.name} 완료", flush=True)
        relative = relative_changes(groups)
        totals = {
            m: str(sum((D(r["pnl_change"]) for r in rows), D(0))) for m, rows in groups.items()
        }
        relative_total = sum((D(r["relative_delay_change"]) for r in relative), D(0))
        if relative_total != D(totals["liquidation-buffer"]) - D(totals["breakout-v1"]):
            raise ValueError("상대 손익 변화 합계 불일치")
        write_json(
            output / "results.json",
            {
                "models": groups,
                "total_pnl_changes": totals,
                "relative_changes": relative,
                "relative_delay_change": str(relative_total),
                "promotion_candidate": False,
            },
        )
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_diagnostic",
                "checked_results": checked,
                "replayed_results": replayed,
                "promotion_candidate": False,
                "live_enabled": False,
            },
        )
        print(f"실험69 완료: {checked}결과 검증, {replayed}결과 관찰 재현, 승격 없음", flush=True)
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험69 체결 지연 경로 진단 · 실계좌 접근 없음")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.reference, args.output)
