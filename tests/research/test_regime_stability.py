import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import torch
from test_learning import candles

from evergreen.research.learning.models import Samples
from evergreen.research.learning.regime import (
    load_regime,
    make_regime_samples,
    split_samples,
    train_regime,
)


def test_regime_seed_is_recorded_and_reproducible(tmp_path: Path) -> None:
    torch.manual_seed(1)
    x = torch.randn(90, 32, 5)
    y = torch.tensor([0, 1, 2] * 30)
    times = tuple(b.close_time for b in candles(200))
    training = Samples(x[:60], y[:60], times[:60], times[24:84])
    validation = Samples(x[60:], y[60:], times[100:130], times[124:154])
    models = [
        train_regime([training], validation, tmp_path / str(i), seed=seed, balanced=True, epochs=2)
        for i, seed in enumerate((17, 29, 29))
    ]
    assert not torch.equal(models[0].probabilities(x), models[1].probabilities(x))
    assert torch.equal(models[1].probabilities(x), models[2].probabilities(x))
    assert json.loads((tmp_path / "1" / "training.json").read_text())["seed"] == 29
    restored = load_regime(tmp_path / "1", times[-1])
    assert torch.equal(models[1].probabilities(x), restored.probabilities(x))
    with pytest.raises(ValueError, match="경계"):
        load_regime(tmp_path / "1", times[153])
    with (tmp_path / "1" / "weights.pt").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="해시"):
        load_regime(tmp_path / "1", times[-1])
    with pytest.raises(ValueError, match="학습 설정"):
        train_regime([training], validation, tmp_path / "invalid", seed=-1)


def test_72h_labels_and_purge_follow_horizon() -> None:
    data = candles(400)
    samples = make_regime_samples(data, 72)
    assert all(
        b - a == timedelta(hours=72)
        for a, b in zip(samples.signal_times, samples.label_times, strict=True)
    )
    baseline = make_regime_samples(data, 24)
    assert torch.equal(samples.features, baseline.features[: len(samples.features)])
    end = samples.signal_times[140]
    subset = split_samples(samples, samples.signal_times[0], end)
    assert max(subset.label_times) < end
    with pytest.raises(ValueError, match="기간"):
        make_regime_samples(data, 48)


def test_frozen_recent_evaluation_uses_saved_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evergreen.research.experiments import regime_stability as study

    data = candles(361)
    offset = study.RECENT_START - timedelta(hours=169) - data[0].open_time
    bars = [
        b.model_copy(
            update={
                "open_time": b.open_time + offset,
                "fetched_at": datetime(2026, 9, 10, tzinfo=UTC),
            }
        )
        for b in data
    ]
    monkeypatch.setattr(study, "load_dataset", lambda _: (bars, "fixture"))
    torch.manual_seed(3)
    x = torch.randn(90, 32, 5)
    y = torch.tensor([0, 1, 2] * 30)
    times = tuple(b.close_time for b in data)
    train_regime(
        [Samples(x[:60], y[:60], times[:60], times[24:84])],
        Samples(x[60:], y[60:], times[100:130], times[124:154]),
        tmp_path / "folds" / "2026-07-01" / "model",
        epochs=1,
        balanced=True,
    )
    study.recent_evaluation(tmp_path, tmp_path)
    metadata = json.loads((tmp_path / "recent" / "evaluation.json").read_text())
    assert metadata["dataset_sha256"] == "fixture"
    assert sum(metadata["classification"]["label_counts"]) == 168
    assert len(study.candidate_rows(tmp_path / "recent")) == 12
    with pytest.raises(ValueError, match="기간"):
        study.recent_evaluation(tmp_path, tmp_path, 72)
    monkeypatch.setattr(study, "load_dataset", lambda _: (bars[1:], "fixture"))
    with pytest.raises(ValueError, match="구간"):
        study.recent_evaluation(tmp_path, tmp_path)


def test_seed_orchestration_missing_recent_and_paired_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evergreen.market import write_json
    from evergreen.research.experiments import regime
    from evergreen.research.experiments import regime_stability as study
    from evergreen.research.experiments.regime_followup import evaluate_ablations

    data = candles(220)
    part = regime.Evaluation(data[169].open_time, data, {b.close_time: 2 for b in data[168:]})
    seen = []

    def training(
        source: Path, output: Path, *, seed: int, horizon: int, balanced: bool, followups: bool
    ) -> dict[str, bool]:
        assert balanced and followups and horizon == 72
        seen.append(seed)
        output.mkdir()
        (output / "datasets").mkdir()
        write_json(output / "protocol.json", {"seed": seed})
        write_json(output / "datasets" / "coverage.json", {"raw_sha256": "same"})
        block = output / "continuous" / "block-000"
        regime.evaluate(part, block)
        evaluate_ablations(part, block)
        return {"live_promotion": False}

    def missing(*args: object) -> None:
        raise ValueError("recent missing")

    monkeypatch.setattr(study, "run_regime_study", training)
    monkeypatch.setattr(study, "recent_evaluation", missing)
    output = tmp_path / "study"
    study.run_stability(tmp_path, tmp_path, output, horizon=72)
    assert seen == list(study.SEEDS)
    results = json.loads((output / "results.json").read_text())
    assert len(results["historical"]) == 12
    assert not results["recent"] and not results["seed_stability"]
    assert "실험 13" in (output / "summary.md").read_text()
    assert not json.loads((output / "status.json").read_text())["recent_complete"]
    studies = [output / f"seed-{seed}" for seed in study.SEEDS]
    (studies[-1] / "datasets" / "coverage.json").write_text('{"raw_sha256":"changed"}')
    with pytest.raises(ValueError, match="원본"):
        study.summarize(studies)
    with pytest.raises(FileExistsError):
        study.run_stability(tmp_path, tmp_path, output)


@pytest.mark.parametrize("error", [KeyError("missing metadata"), TypeError("invalid metadata")])
def test_malformed_recent_artifact_records_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    from evergreen.research.experiments import regime_stability as study

    def training(source: Path, output: Path, **kwargs: object) -> dict[str, bool]:
        output.mkdir()
        return {"live_promotion": False}

    def invalid(*args: object) -> None:
        raise error

    monkeypatch.setattr(study, "run_regime_study", training)
    monkeypatch.setattr(study, "recent_evaluation", invalid)
    output = tmp_path / "study"
    with pytest.raises(type(error)):
        study.run_stability(tmp_path, tmp_path, output)
    assert (output / "failure.json").exists()
    assert not (output / "status.json").exists()
