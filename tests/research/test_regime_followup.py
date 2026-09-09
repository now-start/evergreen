import json
from pathlib import Path

import pytest
import torch
from test_learning import candles

from evergreen.research.learning.models import Samples
from evergreen.research.learning.regime import classification, train_regime
from evergreen.strategies import breakout
from evergreen.strategies.regime import RegimeRouter


def test_cash_policy_blocks_flat_entry_but_preserves_breakout_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(breakout, "target", lambda history, holding: "buy" if not holding else None)
    router = RegimeRouter("sideways-cash")
    assert router.target([], False, 1) is None
    assert router.target([], False, 2) == "buy"
    assert router.target([], True, 1) is None
    assert router.target([], True, 0) == "sell"


def test_entry_filter_never_overrides_breakout_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(breakout, "target", lambda history, holding: "buy" if not holding else None)
    router = RegimeRouter("breakout-filter")
    for regime in (-1, 0, 1):
        assert router.target([], False, regime) is None
    assert router.target([], False, 2) == "buy"
    assert router.target([], True, 0) is None
    monkeypatch.setattr(breakout, "target", lambda history, holding: "sell")
    assert router.target([], True, -1) == "sell"


def test_balanced_training_weights_use_training_counts_only(tmp_path: Path) -> None:
    torch.manual_seed(8)
    x = torch.randn(90, 32, 5)
    labels = torch.tensor([0] * 10 + [1] * 40 + [2] * 10 + [0, 1, 2] * 10)
    times = tuple(b.close_time for b in candles(200))
    training = Samples(x[:60], labels[:60], times[:60], times[24:84])
    validation = Samples(x[60:], labels[60:], times[100:130], times[124:154])
    model = train_regime([training], validation, tmp_path / "model", epochs=2, balanced=True)
    metadata = json.loads((tmp_path / "model" / "training.json").read_text())
    assert metadata["class_weights"] == [2, 0.5, 2]
    assert metadata["validation_objective"] == "unweighted_cross_entropy"
    assert (
        metadata["best_epoch"]
        == min(metadata["history"], key=lambda row: row["validation_loss"])["epoch"]
    )
    metrics = classification(validation.labels, model.probabilities(validation.features), 1)
    assert isinstance(metrics["precision"], list) and len(metrics["precision"]) == 3
    assert isinstance(metrics["recall"], list) and len(metrics["recall"]) == 3


def test_followup_orchestration_and_complete_paired_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evergreen.market import write_json
    from evergreen.research.experiments import regime
    from evergreen.research.experiments.regime_followup import (
        aggregate,
        evaluate_ablations,
        run_followup,
    )

    data = candles(220)
    part = regime.Evaluation(data[169].open_time, data, {b.close_time: 2 for b in data[168:]})
    modes = []

    def study(source: Path, output: Path, *, balanced: bool, followups: bool) -> dict[str, bool]:
        assert followups
        modes.append(balanced)
        output.mkdir()
        (output / "datasets").mkdir()
        write_json(output / "datasets" / "coverage.json", {"raw_sha256": {"fixture": "same"}})
        block = output / "continuous" / "block-000"
        regime.evaluate(part, block)
        evaluate_ablations(part, block)
        write_json(
            output / "results.json",
            {"classification": [{"confusion_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}]},
        )
        return {"live_promotion": False}

    monkeypatch.setattr(regime, "run_regime_study", study)
    output = tmp_path / "followup"
    run_followup(tmp_path, output)
    assert modes == [False, True]
    results = json.loads((output / "comparisons.json").read_text())
    assert len(results["summary"]) == len(results["blocks"]) == 56
    assert "누적 수익률이 아닙니다" in (output / "summary.md").read_text()
    assert json.loads((output / "status.json").read_text())["live_enabled"] is False
    with pytest.raises(FileExistsError):
        run_followup(tmp_path, output)
    (output / "balanced" / "datasets" / "coverage.json").write_text(
        json.dumps({"raw_sha256": {"fixture": "changed"}})
    )
    with pytest.raises(ValueError, match="데이터"):
        aggregate(output)


def test_followup_failure_does_not_write_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evergreen.research.experiments import regime
    from evergreen.research.experiments.regime_followup import run_followup

    def fail(*args: object, **kwargs: object) -> dict[str, bool]:
        raise ValueError("training failed")

    monkeypatch.setattr(regime, "run_regime_study", fail)
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="training failed"):
        run_followup(tmp_path, output)
    assert (output / "failure.json").exists()
    assert not (output / "status.json").exists()
