import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
import torch
from test_learning import candles

from evergreen.market import Candle, write_json
from evergreen.research.learning.models import Samples, window_features
from evergreen.research.learning.regime import (
    TrainedRegime,
    load_regime,
    make_regime_samples,
    sample_identity,
    train_regime,
)
from evergreen.research.learning.regime_features import FeatureSet, regime_features


def test_features_are_causal_and_controls_share_timestamps() -> None:
    data = candles(300)
    short, short_times = window_features(data)
    legacy, legacy_times = regime_features(data)
    assert torch.equal(legacy, short) and legacy_times == short_times
    control, times = regime_features(data, "short-matched")
    long, long_times = regime_features(data, "long")
    assert times == long_times == short_times[145:]
    assert long.shape == control.shape == (101, 32, 10)
    assert times[0] == data[199].close_time
    assert torch.equal(control[:, :, :5], short[145:])
    assert torch.equal(long[:, :, :5], control[:, :, :5])
    assert torch.count_nonzero(control[:, :, 5:]) == 0
    assert float(long[0, -1, 7]) == pytest.approx(math.log(float(data[199].close / data[31].close)))
    assert float(long[0, -1, 8]) == pytest.approx(
        math.log(
            float(
                sum((b.close for b in data[152:200]), Decimal(0))
                / 48
                / (sum((b.close for b in data[32:200]), Decimal(0)) / 168)
            )
        )
    )
    for count in (200, 225, 299):
        prefix, prefix_times = regime_features(data[:count], "long")
        assert torch.equal(prefix, long[: count - 199])
        assert prefix_times == times[: count - 199]
    with pytest.raises(ValueError, match="준비"):
        regime_features(data[:199], "long")
    with pytest.raises(ValueError, match="알 수 없는"):
        regime_features(data, "unknown")  # type: ignore[arg-type]


def test_matched_labels_and_training_restore(tmp_path: Path) -> None:
    data = candles(400)
    left = make_regime_samples(data, feature_set="short-matched")
    right = make_regime_samples(data, feature_set="long")
    assert sample_identity([left]) == sample_identity([right])
    assert torch.equal(left.labels, right.labels)
    assert all(
        b - a == timedelta(hours=24)
        for a, b in zip(right.signal_times, right.label_times, strict=True)
    )
    for mode, samples in (("short-matched", left), ("long", right)):
        training = Samples(
            samples.features[:60],
            torch.tensor([0, 1, 2] * 20),
            samples.signal_times[:60],
            samples.label_times[:60],
        )
        validation = Samples(
            samples.features[100:130],
            torch.tensor([0, 1, 2] * 10),
            samples.signal_times[100:130],
            samples.label_times[100:130],
        )
        model = train_regime([training], validation, tmp_path / mode, epochs=2, feature_set=mode)  # type: ignore[arg-type]
        restored = load_regime(tmp_path / mode, data[-1].close_time)
        predicted, probabilities = model.predict(data)
        actual, loaded = restored.predict(data)
        assert actual == predicted and torch.equal(loaded, probabilities)
        metadata = json.loads((tmp_path / mode / "training.json").read_text())
        assert metadata["feature_channels"] == 10 and metadata["feature_set"] == mode
        assert metadata["training_sample_sha256"] == sample_identity([training])


def test_feature_study_trains_and_checks_matched_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evergreen.research.experiments import regime
    from evergreen.research.experiments import regime_features as study

    dates = [datetime(2020 + i // 4, (i % 4) * 3 + 1, 1, tzinfo=UTC) for i in range(10)]
    blocks = []
    for date in (dates[5], dates[7], dates[8]):
        block = []
        for i in range(500):
            price = Decimal(str(100 + 10 * math.sin(i / 12)))
            block.append(
                Candle(
                    open_time=date + timedelta(hours=i),
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=Decimal(10),
                    quote_volume=Decimal(1000),
                    fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
                )
            )
        blocks.append(block)

    def data(source: Path, output: Path) -> list[list[Candle]]:
        output.mkdir()
        write_json(output / "coverage.json", {"quality": {"missing": ["test-gap"]}})
        return blocks

    def training(
        samples: list[Samples],
        validation: Samples,
        output: Path,
        *,
        balanced: bool,
        seed: int,
        feature_set: FeatureSet,
    ) -> TrainedRegime:
        return train_regime(
            samples,
            validation,
            output,
            epochs=2,
            patience=1,
            balanced=balanced,
            seed=seed,
            feature_set=feature_set,
        )

    monkeypatch.setattr(regime, "quarters", lambda: list(pairwise(dates)))
    monkeypatch.setattr(regime, "contiguous_blocks", data)
    monkeypatch.setattr(regime, "train_regime", training)
    output = tmp_path / "study"
    study.run_feature_study(tmp_path, output)
    results = json.loads((output / "results.json").read_text())
    assert len(results["paired"]) == 12 and not results["live_promotion"]
    assert len(list(output.glob("*/seed-*/folds/*/model/weights.pt"))) == 6
    assert "누적 수익률이 아니며" in (output / "summary.md").read_text()
    assert not json.loads((output / "status.json").read_text())["live_enabled"]
    target = next((output / "long/seed-17").glob("folds/*/model/training.json"))
    metadata = json.loads(target.read_text())
    metadata["training_sample_sha256"] = "mismatch"
    target.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="표본"):
        study.compare(output)
    monkeypatch.setattr(study, "compare", lambda _: (_ for _ in ()).throw(KeyError("missing")))
    monkeypatch.setattr(study, "run_regime_study", lambda *args, **kwargs: None)
    with pytest.raises(KeyError):
        study.run_feature_study(tmp_path, tmp_path / "failed")
    assert (tmp_path / "failed/failure.json").exists()
    assert not (tmp_path / "failed/status.json").exists()
