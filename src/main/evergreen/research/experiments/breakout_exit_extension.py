"""Experiment 52: same-prefix exit counterfactuals and purged sample sufficiency, offline only."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import torch

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Costs, ExitDecision, Fill, Result, run_backtest
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS, period_samples
from evergreen.research.experiments.regime_data import contiguous_blocks, quarters
from evergreen.research.learning.breakout_meta import eligible
from evergreen.research.learning.meta import sample_counts
from evergreen.research.learning.models import Samples
from evergreen.research.report import source_identity

HOUR = timedelta(hours=1)
CAPITAL = Decimal(1000000)


def simulate(
    bars: list[Candle],
    start: datetime,
    pricing: Costs,
    *,
    lookback: int = 24,
    delay: int = 0,
    events: list[ExitDecision] | None = None,
    extend: datetime | None = None,
) -> Result:
    return run_backtest(
        bars,
        start,
        CAPITAL,
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
        breakout_entry_stop_trend_lookback=lookback,
        exit_decisions=events,
        extend_exit_at=extend,
    )


def decision_features(bars: list[Candle], decision: ExitDecision) -> dict[str, str]:
    """Explicit feature allowlist; no outcome, future fill, or future candle is consulted."""
    history = [b for b in bars if b.close_time <= decision.signal_time]
    if len(history) < 200 or history[-1].close_time != decision.signal_time:
        raise ValueError("결정 입력의 확정 봉 이력이 부족합니다")
    current = history[-1]
    tr = max(decision.entry_true_range, prior_mean_true_range(history))
    if tr <= 0 or decision.equity <= 0:
        raise ValueError("결정 입력의 양수 정규화 기준이 없습니다")
    recent = sum((b.close for b in history[-25:-1]), Decimal(0)) / 24
    previous_short = sum((b.close for b in history[-49:-25]), Decimal(0)) / 24
    previous_long = sum((b.close for b in history[-193:-25]), Decimal(0)) / 168
    volume = sum((b.volume for b in history[-25:-1]), Decimal(0)) / 24
    features = {
        **{f"return_{n}h": current.close / history[-n - 1].close - 1 for n in (1, 6, 24, 168)},
        "bar_body": current.close / current.open - 1,
        "bar_range": (current.high - current.low) / tr,
        "volume_ratio_24": current.volume / volume if volume > 0 else Decimal(0),
        "tr_price_ratio": tr / current.close,
        "short_gap_tr": (recent - previous_short) / tr,
        "long_gap_tr": (recent - previous_long) / tr,
        "entry_tr_ratio": decision.entry_true_range / tr,
        "entry_return": current.close / decision.entry_reference - 1,
        "peak_distance_tr": (decision.trailing_peak - current.close) / tr,
        "threshold_distance_tr": (current.close - decision.threshold) / tr,
        "account_drawdown": 1 - decision.equity / decision.account_peak,
        "account_max_drawdown": decision.max_drawdown,
        "holding_hours": Decimal((decision.signal_time - decision.entry_fill.time) // HOUR),
        "long_breaches": Decimal(decision.long_breaches),
    }
    if any(not value.is_finite() for value in features.values()):
        raise ValueError("유한하지 않은 결정 입력입니다")
    return {key: str(value) for key, value in features.items()}


def position_exit(result: Result, decision: ExitDecision) -> Fill | None:
    entries = [i for i, fill in enumerate(result.fills) if fill == decision.entry_fill]
    if len(entries) != 1:
        raise ValueError("동일 실제 진입 체결을 찾을 수 없습니다")
    index = entries[0] + 1
    if index == len(result.fills):
        return None
    fill = result.fills[index]
    if fill.side != "sell" or fill.quantity != decision.btc or fill.btc_after != 0:
        raise ValueError("해당 포지션 첫 매도의 전량 청산 정합성 오류")
    if fill.cash_after != decision.cash + (fill.price * fill.quantity - fill.fee):
        raise ValueError("분기 현금과 실제 매도 체결의 정합성 오류")
    return fill


def label_record(
    decision: ExitDecision,
    original: Result,
    extended: Result,
    *,
    delay: int = 0,
) -> dict[str, Any]:
    a, b = position_exit(original, decision), position_exit(extended, decision)
    record: dict[str, Any] = {
        "exit_a": a.model_dump(mode="json") if a else None,
        "exit_b": b.model_dump(mode="json") if b else None,
        "label": None,
        "label_time": None,
        "cash_difference": None,
        "incremental_return": None,
    }
    if a is None or b is None or a.reason == "settlement" or b.reason == "settlement":
        status = "censored_at_gap_or_end"
    elif (
        a.signal_time != decision.signal_time
        or a.reason != "signal"
        or a.time != decision.signal_time + delay * HOUR
    ):
        status = "original_exit_unfilled_or_replaced"
    elif any(
        rejection.side == "sell" and decision.signal_time <= rejection.time <= fill.time
        for result, fill in ((original, a), (extended, b))
        for rejection in result.rejections
    ):
        status = "exit_rejected"
    elif decision.long_breaches >= 2:
        if a != b:
            raise ValueError("동일 청산 결정의 실제 매도가 다릅니다")
        status = "no_disagreement"
    else:
        difference = b.cash_after - a.cash_after
        record.update(
            label=int(difference > 0),
            label_time=(max(a.time, b.time) + HOUR).isoformat(),
            cash_difference=str(difference),
            incremental_return=str(difference / decision.equity),
        )
        status = "completed"
    return {"status": status, **record}


def collect_events(
    bars: list[Candle],
    start: datetime,
    *,
    delay: int = 0,
) -> tuple[Result, list[dict[str, Any]]]:
    decisions: list[ExitDecision] = []
    original = simulate(bars, start, costs(), events=decisions, delay=delay)
    if len({d.entry_fill.time for d in decisions}) != len(decisions):
        raise ValueError("한 포지션에 중복 청산 사건이 있습니다")
    records = []
    for decision in decisions:
        replay: list[ExitDecision] = []
        extended = simulate(
            bars,
            start,
            costs(),
            events=replay,
            extend=decision.signal_time,
            delay=delay,
        )
        matched = [d for d in replay if d.signal_time == decision.signal_time]
        if matched != [decision]:
            raise ValueError("청산 분기 이전 상태·캔들·계좌 경로가 다릅니다")
        features = decision_features(bars, decision)
        records.append(
            {
                "signal_time": decision.signal_time.isoformat(),
                "state": decision.model_dump(mode="json"),
                "state_sha256": hashlib.sha256(decision.model_dump_json().encode()).hexdigest(),
                "features": features,
                "features_sha256": hashlib.sha256(
                    json.dumps(features, sort_keys=True).encode()
                ).hexdigest(),
                "prefix_equal": True,
                **label_record(decision, original, extended, delay=delay),
            }
        )
    return original, records


def audit_folds(records: list[dict[str, Any]]) -> dict[str, Any]:
    completed = sorted(
        (r for r in records if r["status"] == "completed"),
        key=lambda r: r["signal_time"],
    )
    signals = tuple(datetime.fromisoformat(r["signal_time"]) for r in completed)
    ends = tuple(datetime.fromisoformat(r["label_time"]) for r in completed)
    if len(set(signals)) != len(signals) or any(e <= s for s, e in zip(signals, ends, strict=True)):
        raise ValueError("학습 사건 시각이 중복되거나 레이블 가용 시각이 잘못됐습니다")
    # Counts only: no model or feature fitting occurs in this feasibility experiment.
    samples = Samples(
        torch.empty((len(completed), 0)),
        torch.tensor([r["label"] for r in completed], dtype=torch.float32),
        signals,
        ends,
    )
    windows = quarters()
    folds = []
    for i in range(8, len(windows)):
        train_start, validation_start = windows[i - 8][0], windows[i - 2][0]
        test_start, test_end = windows[i]
        counts = {}
        purged = {}
        for name, start, end in (
            ("training", train_start, validation_start),
            ("validation", validation_start, test_start),
            ("test_audit_only", test_start, test_end),
        ):
            counts[name] = sample_counts(period_samples([samples], start, end))
            purged[name] = sum(
                start <= s < end and e >= end for s, e in zip(signals, ends, strict=True)
            )
        folds.append(
            {
                "training_start": train_start.isoformat(),
                "validation_start": validation_start.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                **counts,
                "purged": purged,
                "eligible": eligible(counts["training"], counts["validation"]),
            }
        )
    return {
        "events": len(records),
        "statuses": dict(Counter(r["status"] for r in records)),
        "completed_counts": sample_counts([samples]),
        "folds": folds,
        "eligible_folds": sum(f["eligible"] for f in folds),
        "model_trained": False,
        "profitability_gate_passed": False,
    }


def run_study(raw: Path, reference: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "experiment": "52",
            "reference_experiment": "51",
            "live_enabled": False,
            "label": "same_prefix_first_exit_cash_b_minus_a",
            "event_policy": "first_extra_exit_per_policy50_position_base_costs",
            "training_quarters": 6,
            "validation_quarters": 2,
            "warmup_hours": 200,
            "selection_bias": "policy50_position_and_first_extra_exit_conditioned",
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
            read(reference / "protocol.json")["experiment"] != "51"
            or read(reference / "status.json")["status"] != "completed_partial_coverage"
        ):
            raise ValueError("완료된 실험51 대조가 필요합니다")
        blocks = contiguous_blocks(raw, output / "datasets")
        if read(reference / "datasets/coverage.json") != read(output / "datasets/coverage.json"):
            raise ValueError("원자료 해시·커버리지가 다릅니다")
        intervals = read(reference / "intervals.json")
        paths = sorted((reference / "seed-17/continuous").glob("*/base.breakout-v1.json"))
        if not intervals or len(paths) != len(intervals):
            raise ValueError("기존 평가 계좌 구간이 누락됐습니다")
        (output / "accounts").mkdir()
        (output / "controls").mkdir()
        records: list[dict[str, Any]] = []
        accounts = []
        parity = 0
        cutoff = datetime(2022, 1, 1, tzinfo=UTC)
        # Historical prefix supplies training coverage only, never resets an evaluation account.
        for index, block in enumerate(blocks):
            bars = [b for b in block if b.open_time < cutoff]
            if len(bars) <= 200:
                continue
            start = bars[200].open_time
            original, events = collect_events(bars, start)
            name = f"history-{index:03d}"
            records.extend({"account": name, **event} for event in events)
            accounts.append(
                {
                    "account": name,
                    "start": start.isoformat(),
                    "end": original.end.isoformat(),
                    "events": len(events),
                    "halted": original.halted,
                    "role": "historical_training_only",
                }
            )
            write_json(output / "accounts" / f"{name}.json", original.model_dump(mode="json"))
            print(f"학습 이력 {name}: 사건 {len(events)}", flush=True)
        for index, (interval, path) in enumerate(zip(intervals, paths, strict=True)):
            start, end = (datetime.fromisoformat(interval[k]) for k in ("start", "end"))
            old = read(path)
            if any(
                datetime.fromisoformat(old[k]) != value
                for k, value in (("start", start), ("end", end))
            ):
                raise ValueError("기존 평가 계좌 경계 불일치")
            matching = [
                b
                for b in blocks
                if b[0].open_time <= start - 200 * HOUR and b[-1].close_time >= end
            ]
            if len(matching) != 1 or start < cutoff:
                raise ValueError("평가 계좌의 연속 이력이 없거나 학습 이력과 겹칩니다")
            bars = [b for b in matching[0] if start - 200 * HOUR <= b.open_time < end]
            original, events = collect_events(bars, start)
            name = f"evaluation-{index:03d}"
            (output / "controls" / name).mkdir()
            records.extend({"account": name, **event} for event in events)
            accounts.append(
                {
                    "account": name,
                    **interval,
                    "events": len(events),
                    "halted": original.halted,
                    "role": "unchanged_evaluation_account",
                }
            )
            for scenario, fee, slip, delay in SCENARIOS:
                for model, lookback in (
                    ("entry-stop-budget", 24),
                    ("entry-stop-trend-context", 168),
                ):
                    result = (
                        original
                        if scenario == "base" and lookback == 24
                        else simulate(
                            bars,
                            start,
                            costs(fee, slip),
                            lookback=lookback,
                            delay=delay,
                        )
                    )
                    filename = f"{scenario}.{model}.json"
                    if result.model_dump(mode="json") != read(path.parent / filename):
                        raise ValueError(f"50/51 전체 결과 재현 실패: {name}/{filename}")
                    write_json(
                        output / "controls" / name / filename, result.model_dump(mode="json")
                    )
                    parity += 1
            print(f"평가 이력 {name}: 사건 {len(events)}, 대조 {parity}개 일치", flush=True)
        audit = audit_folds(records)
        audit.update(
            control_results_identical=parity,
            accounts=accounts,
            evaluation_hours=sum(
                int((datetime.fromisoformat(r["end"]) - datetime.fromisoformat(r["start"])) // HOUR)
                for r in intervals
            ),
        )
        write_json(output / "events.json", records)
        write_json(output / "results.json", audit)
        write_json(output / "inputs.json", hashes)
        write_json(
            output / "status.json",
            {
                "status": "completed_partial_coverage",
                "baseline_parity": True,
                "live_enabled": False,
                "model_trained": False,
            },
        )
        print(
            f"청산 연장 데이터 검증 완료: {audit['completed_counts']}, 학습 가능 {audit['eligible_folds']}분기",
            flush=True,
        )
    except Exception as error:
        write_json(output / "failure.json", {"error": str(error), "live_enabled": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실험52 청산 연장 표본 검증 · 실계좌 접근 없음")
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.raw, args.reference, args.output)
