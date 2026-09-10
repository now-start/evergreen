"""Experiment 70: frozen policy 57 in a newly evaluated older historical window."""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from evergreen.market import Candle, Quality, UpbitCandle, validate_candles, write_json
from evergreen.research.backtest import Result, run_backtest
from evergreen.research.experiments.breakout_errors import trade_pnl
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.strategy_family import simulate, summarize
from evergreen.research.report import source_identity

START = datetime(2018, 1, 1, tzinfo=UTC)
END = datetime(2020, 1, 1, tzinfo=UTC)
MODELS = ("breakout-v1", "cash", "buy-hold", "liquidation-buffer")


def raw_blocks(
    source: Path, start: datetime, end: datetime
) -> tuple[list[list[Candle]], Quality, dict[str, str]]:
    hashes: dict[str, str] = {}
    manifest_path = source / "quality.json"
    content = manifest_path.read_bytes()
    hashes[str(manifest_path.resolve())] = hashlib.sha256(content).hexdigest()
    manifest = json.loads(content)
    if (datetime.fromisoformat(manifest["start"]), datetime.fromisoformat(manifest["end"])) != (
        start,
        end,
    ):
        raise ValueError("수집 범위가 고정 평가 범위와 다릅니다")
    if not manifest["requests"] or manifest.get("quality") is None:
        raise ValueError("완료되지 않은 수집 요청은 시간봉 누락으로 처리할 수 없습니다")
    records: list[Candle] = []
    for request in manifest["requests"]:
        name = request["raw"]
        if Path(name).name != name or request["status"] != 200:
            raise ValueError("원본 경로·HTTP 상태 오류")
        path = source / "raw" / name
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != request["sha256"]:
            raise ValueError("원본 해시 불일치")
        hashes[str(path.resolve())] = digest
        batch = TypeAdapter(list[UpbitCandle]).validate_python(json.loads(content, parse_float=D))
        records.extend(b.candle(datetime.fromisoformat(request["fetched_at"])) for b in batch)
    bars, quality = validate_candles(records, start, end)
    if not bars or quality.conflicts or quality.incomplete:
        raise ValueError("빈 원자료·가격 충돌·미확정 봉")
    blocks: list[list[Candle]] = []
    for bar in bars:
        if not blocks or blocks[-1][-1].close_time != bar.open_time:
            blocks.append([])
        blocks[-1].append(bar)
    return blocks, quality, hashes


def run_study(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "70",
            "prespecified_in": "docs/delay-path-69.md",
            "start": START.isoformat(),
            "end": END.isoformat(),
            "warmup_hours": 200,
            "models": MODELS,
            "scenarios": SCENARIOS,
            "capital": "1000000",
            "evaluation": "older_historical_generalization_not_prospective_validation",
            "gap_policy": "all_continuous_blocks_after_200_hour_warmup_no_interpolation",
            "trained_models": 0,
            "live_enabled": False,
            **source_identity(),
        },
    )
    try:
        blocks, quality, hashes = raw_blocks(source, START - timedelta(hours=200), END)
        groups: dict[str, dict[str, list[Result]]] = {
            s: {m: [] for m in MODELS} for s, *_ in SCENARIOS
        }
        intervals: list[dict[str, Any]] = []
        checks = 0
        for i, block in enumerate(blocks):
            start = max(START, block[0].open_time + timedelta(hours=200))
            end = min(END, block[-1].close_time)
            if start >= end:
                continue
            bars = [b for b in block if start - timedelta(hours=200) <= b.open_time < end]
            directory = output / "accounts" / f"block-{i:03d}"
            directory.mkdir(parents=True)
            dataset = output / "datasets" / f"block-{i:03d}"
            dataset.mkdir(parents=True)
            raw = "".join(b.model_dump_json() + "\n" for b in bars).encode()
            with (dataset / "candles.jsonl").open("xb") as stream:
                stream.write(raw)
            write_json(
                dataset / "quality.json",
                {
                    "status": "passed",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "start": bars[0].open_time.isoformat(),
                    "end": end.isoformat(),
                    "as_of": datetime.now(UTC).isoformat(),
                },
            )
            intervals.append(
                {
                    "account": directory.name,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "hours": (end - start).total_seconds() / 3600,
                }
            )
            for s, fee, slip, delay in SCENARIOS:
                for name in MODELS:
                    result = (
                        run_backtest(
                            bars,
                            start,
                            D(1000000),
                            costs(fee, slip),
                            "buy-hold",
                            extra_delay_bars=delay,
                        )
                        if name == "buy-hold"
                        else simulate(bars, start, name, costs(fee, slip), delay)
                    )
                    data = result.model_dump(mode="json")
                    if name == "buy-hold":
                        # Benchmark buy has no strategy signal timestamp, unlike active strategies.
                        pnl = sum(
                            (
                                (
                                    D(f["price"]) * D(f["quantity"])
                                    if f["side"] == "sell"
                                    else -D(f["price"]) * D(f["quantity"])
                                )
                                - D(f["fee"])
                                for f in data["fills"]
                            ),
                            D(0),
                        )
                        if abs(pnl - (result.final_equity - result.initial_capital)) > D(".000001"):
                            raise ValueError("매수보유 손익 불일치")
                    else:
                        trade_pnl(data)
                    write_json(directory / f"{s}.{name}.json", data)
                    groups[s][name].append(result)
                    checks += 1
            print(
                f"실험70 {directory.name}: {(end - start).total_seconds() / 3600:g}시간 평가",
                flush=True,
            )
        if not intervals:
            raise ValueError("준비봉 이후 평가할 연속 계좌가 없습니다")
        summaries = {
            s: {m: summarize(rows, g["breakout-v1"], s) for m, rows in g.items()}
            for s, g in groups.items()
        }
        gate = all(g["liquidation-buffer"]["gate"] for g in summaries.values())
        evaluated = sum(row["hours"] for row in intervals)
        total = (END - START).total_seconds() / 3600
        write_json(output / "intervals.json", intervals)
        write_json(
            output / "coverage.json",
            {
                "quality": quality.model_dump(mode="json"),
                "observed_blocks": [
                    {
                        "start": b[0].open_time.isoformat(),
                        "end": b[-1].close_time.isoformat(),
                        "hours": len(b),
                    }
                    for b in blocks
                ],
                "evaluated_hours": evaluated,
                "total_hours": total,
                "accounts": len(intervals),
                "full_period_passed": quality.valid and evaluated == total,
            },
        )
        write_json(
            output / "results.json",
            {"scenarios": summaries, "historical_gate": gate, "live_enabled": False},
        )
        for path in output.rglob("*.json*"):
            hashes[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_historical_evaluation",
                "pnl_checks": checks,
                "historical_gate": gate,
                "accounts": len(intervals),
                "live_enabled": False,
            },
        )
        print(
            f"실험70 완료: {len(intervals)}계좌, {evaluated:g}/{total:g}시간, 과거구간 게이트={gate}",
            flush=True,
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험70 고정57 역사 구간 평가 · 실계좌 접근 없음")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.source, args.output)
