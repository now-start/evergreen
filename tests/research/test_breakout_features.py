import math
from decimal import Decimal
from pathlib import Path

import pytest
import torch
from test_breakout_meta import event_bars
from test_learning import candles

from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.learning.breakout_meta import breakout_events
from evergreen.research.learning.models import (
    FeatureSet,
    Samples,
    load_model,
    train_model,
    window_features,
)


def test_context_formula_and_prefix_causality() -> None:
    bars = candles(260)
    x, times = window_features(bars, "breakout-context")
    assert x.shape == (61, 32, 5) and times[0] == bars[199].close_time
    i = 199
    closes = [float(b.close) for b in bars]
    returns = [math.log(closes[t] / closes[t - 1]) for t in range(i - 23, i + 1)]
    average = sum(returns) / 24
    expected = [
        math.log(closes[i] / max(float(b.high) for b in bars[i - 168 : i])),
        math.log(closes[i] / min(float(b.low) for b in bars[i - 48 : i])),
        math.log((sum(closes[i - 47 : i + 1]) / 48) / (sum(closes[i - 167 : i + 1]) / 168)),
        math.log1p(float(bars[i].volume))
        - math.log1p(sum(float(b.volume) for b in bars[i - 24 : i]) / 24),
        math.sqrt(sum((r - average) ** 2 for r in returns) / 24),
    ]
    assert x[0, -1].tolist() == pytest.approx(expected)
    prefix, earlier = window_features(bars[:230], "breakout-context")
    assert torch.equal(x[: len(prefix)], prefix) and times[: len(prefix)] == earlier
    # Current high/low must not leak into the prior breakout/exit boundaries.
    changed = bars.copy()
    changed[i] = bars[i].model_copy(update={"high": bars[i].high * 10, "low": bars[i].low / 10})
    other, _ = window_features(changed, "breakout-context")
    assert torch.equal(x[0], other[0])
    with pytest.raises(ValueError, match="200"):
        window_features(bars[:199], "breakout-context")


def test_control_and_context_share_event_labels_and_timestamps() -> None:
    bars = event_bars()
    original, times = window_features(bars)
    control, matched = window_features(bars, "short-200")
    assert torch.equal(original[145:], control) and times[145:] == matched
    short, events = breakout_events(bars, costs(), "short-200")
    context, other = breakout_events(bars, costs(), "breakout-context")
    assert events == other and short.signal_times == context.signal_times
    assert short.label_times == context.label_times and torch.equal(short.labels, context.labels)
    assert short.signal_times[0] == bars[199].close_time
    assert short.features.shape == context.features.shape
    assert not torch.equal(short.features, context.features)


@pytest.mark.parametrize("feature_set", ["short-200", "breakout-context"])
@pytest.mark.parametrize("architecture", ["mlp-v1", "cnn-v1"])
def test_context_checkpoint_reuses_prediction_transform(
    tmp_path: Path, feature_set: FeatureSet, architecture: str
) -> None:
    bars = candles(260)
    x, times = window_features(bars, feature_set)
    sample = Samples(x, torch.arange(len(x)).remainder(2).float(), times, times)
    model = train_model(
        [sample], architecture, tmp_path / "model", epochs=2, feature_set=feature_set
    )
    restored = load_model(tmp_path / "model")
    assert restored.metadata.feature_set == feature_set
    assert restored.predict(bars) == model.predict(bars)
    assert list(restored.predict(bars)) == list(times)
    assert list(restored.predict(bars).values()) == [
        Decimal(str(p)) for p in restored.probabilities(x).tolist()
    ]
