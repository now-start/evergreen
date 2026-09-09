"""Cost-aware breakout event labels with the original uncapped exit and shared simulator."""

import hashlib
import json
from bisect import bisect_left
from datetime import datetime, timedelta
from decimal import Decimal

import torch

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.learning.models import FeatureSet, Samples, feature_warmup, window_features
from evergreen.research.learning.regime import sample_identity
from evergreen.strategies import strategy_target


def breakout_events(
    candles: list[Candle], costs: Costs, feature_set: FeatureSet = "short"
) -> tuple[Samples, list[dict[str, object]]]:
    features, _ = window_features(candles, feature_set)
    warmup = feature_warmup(feature_set)
    # An ordinary exit bounds computation only; an earlier risk exit still owns the label.
    exits = [
        i
        for i in range(168, len(candles) - 1)
        if strategy_target(candles[i - 168 : i + 1], True, "breakout-v1") == "sell"
    ]
    indices, labels, signals, ends = [], [], [], []
    records: list[dict[str, object]] = []
    for i in range(max(168, warmup - 1), len(candles) - 1):
        if strategy_target(candles[i - 168 : i + 1], False, "breakout-v1") != "buy":
            continue
        position = bisect_left(exits, i + 1)
        end = exits[position] + 2 if position < len(exits) else len(candles)
        bars = candles[i - 168 : end]
        approvals = {b.close_time: 0 for b in bars[168:]}
        approvals[candles[i].close_time] = 2
        result = run_backtest(
            bars,
            candles[i].close_time,
            Decimal(1000000),
            costs,
            "regime-mlp-v1",
            regimes=approvals,
            regime_policy="breakout-filter",
        )
        if result.rejections or len(result.fills) != 2:
            raise ValueError("돌파 사건의 단일 진입·청산을 완성하지 못했습니다")
        entry, exit_fill = result.fills
        if entry.side != "buy" or exit_fill.side != "sell":
            raise ValueError("돌파 사건 체결 순서 오류")
        complete = exit_fill.reason != "settlement"
        label_time = exit_fill.time + timedelta(hours=1)
        records.append(
            {
                "signal_time": candles[i].close_time.isoformat(),
                "entry_time": entry.time.isoformat(),
                "exit_time": exit_fill.time.isoformat(),
                "exit_reason": exit_fill.reason,
                "label_time": label_time.isoformat() if complete else None,
                "net_return": str(result.net_return) if complete else None,
                "label": int(result.net_return > 0) if complete else None,
                "status": "completed" if complete else "censored_at_gap_or_end",
            }
        )
        if complete:
            indices.append(i - warmup + 1)
            labels.append(float(result.net_return > 0))
            signals.append(candles[i].close_time)
            ends.append(label_time)
    return Samples(
        features[torch.tensor(indices, dtype=torch.long)],
        torch.tensor(labels, dtype=torch.float32),
        tuple(signals),
        tuple(ends),
    ), records


def eligible(training: dict[str, int], validation: dict[str, int]) -> bool:
    return (
        training["samples"] >= 50
        and validation["samples"] >= 20
        and training["non_overlapping_groups"] >= 20
        and validation["non_overlapping_groups"] >= 5
        and min(
            training["positives"],
            training["negatives"],
            validation["positives"],
            validation["negatives"],
        )
        > 0
    )


def sample_uniqueness(samples: list[Samples]) -> tuple[list[Decimal], dict[str, object]]:
    """Mean inverse hourly concurrency, using only this already-purged split."""
    intervals = []
    counts: dict[int, int] = {}
    for sample in samples:
        for signal, end in zip(sample.signal_times, sample.label_times, strict=True):
            if signal.tzinfo is None or end.tzinfo is None:
                raise ValueError("겹침 가중치 시각에는 시간대가 필요합니다")
            start_seconds, end_seconds = signal.timestamp(), end.timestamp()
            if start_seconds % 3600 or end_seconds % 3600 or end_seconds <= start_seconds:
                raise ValueError("겹침 가중치에는 양수 길이의 정시 구간이 필요합니다")
            start, stop = int(start_seconds // 3600), int(end_seconds // 3600)
            intervals.append((start, stop))
            for hour in range(start, stop):
                counts[hour] = counts.get(hour, 0) + 1
    if not intervals or len({a for a, _ in intervals}) != len(intervals):
        raise ValueError("겹침 가중치 표본이 비었거나 중복됐습니다")
    inverse = {hour: Decimal(1) / count for hour, count in counts.items()}
    values = [
        sum((inverse[t] for t in range(start, stop)), Decimal(0)) / (stop - start)
        for start, stop in intervals
    ]
    return values, {
        "values": [str(value) for value in values],
        "max_concurrency": max(counts.values()),
        "sha256": hashlib.sha256(
            json.dumps(
                [(a, b, str(u)) for (a, b), u in zip(intervals, values, strict=True)]
            ).encode()
        ).hexdigest(),
    }


def payoff_weights(
    events: list[dict[str, object]], samples: list[Samples], *, overlap_adjusted: bool = False
) -> tuple[torch.Tensor, dict[str, object]]:
    """Join only already-purged samples to completed outcomes; never change sample order."""
    completed = [r for r in events if r["status"] == "completed"]
    lookup = {str(r["signal_time"]): r for r in completed}
    if len(lookup) != len(completed):
        raise ValueError("중복 손익 사건입니다")
    returns: list[Decimal] = []
    identities = []
    for sample in samples:
        for signal, end, label in zip(
            sample.signal_times, sample.label_times, sample.labels.tolist(), strict=True
        ):
            event = lookup.get(signal.isoformat())
            if event is None:
                raise ValueError("가중치 사건이 누락됐습니다")
            value = Decimal(str(event["net_return"]))
            if (
                not value.is_finite()
                or str(event["label_time"]) != end.isoformat()
                or event["label"] != label
                or int(value > 0) != label
            ):
                raise ValueError("가중치와 표본 시각·레이블이 일치하지 않습니다")
            returns.append(value)
            identities.append((signal.isoformat(), end.isoformat(), int(label), str(value)))
    magnitudes = [abs(value) for value in returns]
    raw_total = sum(magnitudes, Decimal(0))
    overlap_audit: dict[str, object] = {}
    if overlap_adjusted:
        uniqueness, overlap_audit = sample_uniqueness(samples)
        magnitudes = [m * u for m, u in zip(magnitudes, uniqueness, strict=True)]
    total = sum(magnitudes, Decimal(0))
    if total <= 0:
        raise ValueError("가중치 합계가 양수가 아닙니다")
    audit: dict[str, object] = {
        "events": len(returns),
        "sample_sha256": sample_identity(samples),
        "payoff_sha256": hashlib.sha256(json.dumps(identities).encode()).hexdigest(),
        "weighted_prior": str(
            sum((w for r, w in zip(returns, magnitudes, strict=True) if r > 0), Decimal(0)) / total
        ),
        "top1_share": str(max(magnitudes) / total),
        "top5_share": str(sum(sorted(magnitudes)[-5:], Decimal(0)) / total),
        "absolute_return_sum": str(raw_total),
    }
    if overlap_adjusted:
        audit.update(
            {
                "uniqueness": overlap_audit,
                "adjusted_weight_sum": str(total),
                "weight_sha256": hashlib.sha256(
                    json.dumps([str(v) for v in magnitudes]).encode()
                ).hexdigest(),
            }
        )
    return torch.tensor([float(v) for v in magnitudes], dtype=torch.float32), audit


def payoff_threshold(
    events: list[dict[str, object]], start: datetime, end: datetime
) -> tuple[Decimal, dict[str, object]]:
    selected = [
        r
        for r in events
        if r["status"] == "completed"
        and start <= datetime.fromisoformat(str(r["signal_time"])) < end
        and datetime.fromisoformat(str(r["label_time"])) < end
    ]
    returns = [Decimal(str(r["net_return"])) for r in selected]
    wins, losses = [r for r in returns if r > 0], [-r for r in returns if r <= 0]
    if not wins or not losses or any(not r.is_finite() for r in returns):
        raise ValueError("손익분기 기준에 필요한 두 클래스가 없습니다")
    win, loss = sum(wins, Decimal(0)) / len(wins), sum(losses, Decimal(0)) / len(losses)
    identity = [(r["signal_time"], r["label_time"], r["label"]) for r in selected]
    return loss / (win + loss), {
        "mean_win": str(win),
        "mean_loss": str(loss),
        "events": len(selected),
        "sample_sha256": hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
    }
