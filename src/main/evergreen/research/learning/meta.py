"""Candidate-specific, cost-aware labels using the same portfolio engine as evaluation."""

from datetime import datetime
from decimal import Decimal

import torch

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.learning.models import WARMUP, Samples, window_features
from evergreen.strategies import (
    META_COMPONENTS,
    TIMED_RULES,
    Strategy,
    strategy_target,
    warmup_bars,
)


def event_samples(candles: list[Candle], rule: Strategy, costs: Costs) -> Samples:
    if rule not in TIMED_RULES.values():
        raise ValueError("지원하지 않는 메타 레이블 규칙입니다")
    features, _ = window_features(candles)
    strategy = next(name for name, (_, source) in META_COMPONENTS.items() if source == rule)
    indices: list[int] = []
    labels: list[float] = []
    signals: list[datetime] = []
    ends: list[datetime] = []
    for index in range(warmup_bars(rule) - 1, len(candles) - 13):
        if strategy_target(candles[: index + 1], False, rule) != "buy":
            continue
        # Only this candidate is approved. Subsequent candidates cannot create extra trades.
        segment = candles[index + 1 - warmup_bars(rule) : index + 14]
        scores = {bar.close_time: Decimal(0) for bar in segment[warmup_bars(rule) - 1 :]}
        scores[candles[index].close_time] = Decimal(1)
        result = run_backtest(
            segment,
            candles[index + 1].open_time,
            Decimal(1000000),
            costs,
            strategy,
            predictions=scores,
        )
        if len(result.fills) != 2 or result.fills[-1].reason == "settlement" or result.rejections:
            raise ValueError("후보 거래의 정상 진입·청산 레이블을 완성하지 못했습니다")
        indices.append(index - WARMUP + 1)
        labels.append(float(result.net_return > 0))
        signals.append(candles[index].close_time)
        # Conservative horizon: the full final exit bar is available before a label is used.
        ends.append(candles[index + 13].close_time)
    return Samples(
        features[torch.tensor(indices, dtype=torch.long)],
        torch.tensor(labels, dtype=torch.float32),
        tuple(signals),
        tuple(ends),
    )


def sample_counts(samples: list[Samples]) -> dict[str, int]:
    signals = sorted(
        (signal, end)
        for sample in samples
        for signal, end in zip(sample.signal_times, sample.label_times, strict=True)
    )
    last_end: datetime | None = None
    groups = 0
    for signal, end in signals:
        if last_end is None or signal > last_end:
            groups += 1
            last_end = end
    total = sum(len(sample.labels) for sample in samples)
    positives = sum(int(sample.labels.sum().item()) for sample in samples)
    return {
        "samples": total,
        "positives": positives,
        "negatives": total - positives,
        "non_overlapping_groups": groups,
    }
