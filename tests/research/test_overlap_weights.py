from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
import torch

from evergreen.research.learning.breakout_meta import payoff_weights
from evergreen.research.learning.models import Samples


def fixture(intervals: list[tuple[int, int]]) -> tuple[Samples, list[dict[str, Any]]]:
    origin = datetime(2020, 1, 1, tzinfo=UTC)
    signals = tuple(origin + timedelta(hours=a) for a, _ in intervals)
    ends = tuple(origin + timedelta(hours=b) for _, b in intervals)
    labels = torch.tensor([i % 2 for i in range(len(intervals))]).float()
    samples = Samples(torch.zeros(len(intervals), 32, 5), labels, signals, ends)
    events = [
        {
            "signal_time": s.isoformat(),
            "label_time": e.isoformat(),
            "label": int(y),
            "net_return": ".1" if y else "-.1",
            "status": "completed",
        }
        for s, e, y in zip(signals, ends, labels.tolist(), strict=True)
    ]
    return samples, events


def test_nonoverlap_and_touching_boundaries_preserve_payoff_weights() -> None:
    sample, events = fixture([(0, 2), (2, 4), (5, 7)])
    raw, old = payoff_weights(events, [sample])
    adjusted, audit = payoff_weights(events, [sample], overlap_adjusted=True)
    assert torch.equal(raw, adjusted)
    assert cast(str, audit["weighted_prior"]) == old["weighted_prior"]
    assert cast(dict[str, Any], audit["uniqueness"])["values"] == ["1", "1", "1"]


def test_partial_overlap_is_per_event_mean_inverse_concurrency() -> None:
    sample, events = fixture([(0, 4), (2, 5), (7, 8)])
    weights, audit = payoff_weights(events, [sample], overlap_adjusted=True)
    assert weights.tolist() == pytest.approx([0.075, 0.1 * 2 / 3, 0.1])
    assert [float(x) for x in cast(dict[str, Any], audit["uniqueness"])["values"]] == pytest.approx(
        [0.75, 2 / 3, 1]
    )
    assert float(cast(str, audit["weighted_prior"])) == pytest.approx(
        (0.1 * 2 / 3) / (0.075 + 0.1 * 2 / 3 + 0.1)
    )
    # Reversing order reverses coefficients, not their meaning; blocks are not reset.
    reversed_sample = Samples(
        sample.features.flip(0),
        sample.labels.flip(0),
        sample.signal_times[::-1],
        sample.label_times[::-1],
    )
    other, _ = payoff_weights(events[::-1], [reversed_sample], overlap_adjusted=True)
    assert torch.equal(weights.flip(0), other)
    assert audit["sample_sha256"]
    assert audit["weight_sha256"] and cast(dict[str, Any], audit["uniqueness"])["sha256"]


def test_events_outside_purged_samples_cannot_change_coefficients() -> None:
    sample, events = fixture([(0, 4), (2, 5), (7, 8)])
    subset = Samples(
        sample.features[:1], sample.labels[:1], sample.signal_times[:1], sample.label_times[:1]
    )
    weights, audit = payoff_weights(events, [subset], overlap_adjusted=True)
    assert weights.tolist() == pytest.approx([0.1])
    assert cast(dict[str, Any], audit["uniqueness"])["values"] == ["1"]
    assert Decimal(cast(str, audit["weighted_prior"])) == 0


@pytest.mark.parametrize("intervals", [[(0, 0)], [(2, 1)], [(0, 1.5)], [(0, 2), (0, 3)]])
def test_invalid_intervals_and_duplicates_are_rejected(intervals: list[tuple[int, int]]) -> None:
    sample, events = fixture(intervals)
    with pytest.raises(ValueError):
        payoff_weights(events, [sample], overlap_adjusted=True)
