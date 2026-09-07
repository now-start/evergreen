import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import torch
from torch import Tensor

from evergreen.research.experiments.early_stopping import split_samples
from evergreen.research.learning.models import ChartModel, Samples, load_model, train_model


def samples(start: datetime, *, value: float = 1) -> Samples:
    times = tuple(start + timedelta(hours=i) for i in range(20))
    return Samples(
        torch.full((20, 32, 5), value, dtype=torch.float32),
        torch.zeros(20),
        times,
        tuple(t + timedelta(hours=12) for t in times),
    )


def test_early_stopping_restores_best_epoch_not_last(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    training = samples(datetime(2024, 1, 1, tzinfo=UTC))
    validation = samples(datetime(2024, 3, 1, tzinfo=UTC), value=100)
    values = iter([0.5, 0.4, 0.4, 0.6])
    states: list[dict[str, Tensor]] = []

    def loss(model: ChartModel, x: Tensor, y: Tensor) -> float:
        assert not model.training
        states.append({key: value.detach().clone() for key, value in model.state_dict().items()})
        return next(values)

    monkeypatch.setattr("evergreen.research.learning.models.validation_loss", loss)
    fitted = train_model(
        [training], "mlp-v1", tmp_path / "early", epochs=100, validation=[validation], patience=2
    )
    assert fitted.metadata.epochs == 4
    assert fitted.metadata.max_epochs == 100
    assert fitted.metadata.best_epoch == fitted.metadata.checkpoint_epoch == 2
    assert fitted.metadata.stopped_early
    assert fitted.metadata.validation_losses == [0.5, 0.4, 0.4, 0.6]
    assert torch.equal(fitted.mean, training.features.mean(dim=(0, 1)))
    restored = load_model(tmp_path / "early")
    for key, value in fitted.model.state_dict().items():
        assert torch.equal(value, states[1][key])
        assert torch.equal(value, restored.model.state_dict()[key])
    state = torch.load(tmp_path / "early" / "checkpoint.pt", weights_only=True)
    assert all(item["step"].item() == 2 for item in state["optimizer"]["state"].values())


def test_fixed_epochs_monitors_but_does_not_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = iter([0.5, 0.6, 0.7])
    monkeypatch.setattr(
        "evergreen.research.learning.models.validation_loss", lambda *args: next(values)
    )
    fitted = train_model(
        [samples(datetime(2024, 1, 1, tzinfo=UTC))],
        "cnn-v1",
        tmp_path / "fixed",
        epochs=3,
        validation=[samples(datetime(2024, 3, 1, tzinfo=UTC))],
    )
    assert fitted.metadata.epochs == fitted.metadata.checkpoint_epoch == 3
    assert fitted.metadata.best_epoch == 1
    assert not fitted.metadata.stopped_early


def test_validation_cannot_overlap_training_labels(tmp_path: Path) -> None:
    data = samples(datetime(2024, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="경계"):
        train_model([data], "mlp-v1", tmp_path / "overlap", validation=[data], patience=2)
    assert not (tmp_path / "overlap").exists()
    with pytest.raises(ValueError, match="검증"):
        train_model([data], "mlp-v1", tmp_path / "absent", patience=2)


def test_nonfinite_validation_loss_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "evergreen.research.learning.models.validation_loss", lambda *args: float("nan")
    )
    with pytest.raises(ValueError, match="검증 손실"):
        train_model(
            [samples(datetime(2024, 1, 1, tzinfo=UTC))],
            "mlp-v1",
            tmp_path / "nan",
            validation=[samples(datetime(2024, 3, 1, tzinfo=UTC))],
            patience=2,
        )
    assert not (tmp_path / "nan" / "model.json").exists()


def test_chronological_split_purges_crossing_labels() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    data = samples(start)
    boundary = start + timedelta(hours=15)
    training, validation = split_samples([data], boundary)
    assert len(training[0].labels) == 3
    assert len(validation[0].labels) == 5
    assert max(training[0].label_times) < boundary
    assert min(validation[0].signal_times) == boundary
    with pytest.raises(ValueError, match="분리"):
        split_samples([data], start - timedelta(days=1))


@pytest.mark.parametrize("architecture", ["mlp-v1", "cnn-v1"])
def test_real_validation_keeps_seeded_training_prefix(architecture: str, tmp_path: Path) -> None:
    training = [samples(datetime(2024, 1, 1, tzinfo=UTC))]
    validation = [samples(datetime(2024, 3, 1, tzinfo=UTC), value=2.0)]
    fixed = train_model(training, architecture, tmp_path / "fixed", epochs=3, validation=validation)
    early = train_model(
        training, architecture, tmp_path / "early", epochs=5, validation=validation, patience=2
    )
    count = min(fixed.metadata.epochs, early.metadata.epochs)
    assert fixed.metadata.losses[:count] == early.metadata.losses[:count]
    assert fixed.metadata.validation_losses[:count] == early.metadata.validation_losses[:count]
    assert (
        early.metadata.best_epoch
        == early.metadata.validation_losses.index(min(early.metadata.validation_losses)) + 1
    )
    assert early.metadata.checkpoint_epoch == early.metadata.best_epoch


def test_early_cli_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from evergreen.research.__main__ import main

    def run(training: list[Path], evaluation: list[Path], output: Path) -> None:
        assert len(training) == 3 and len(evaluation) == 4 and output == tmp_path

    monkeypatch.setattr("evergreen.research.experiments.early_stopping.run_early_study", run)
    assert (
        main(
            [
                "early-study",
                "--training",
                "a",
                "b",
                "c",
                "--evaluation",
                "d",
                "e",
                "f",
                "g",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "수익성 최종 검증 아님" in capsys.readouterr().out


@pytest.mark.parametrize("fail_last", [False, True])
def test_early_pipeline_freezes_before_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_last: bool
) -> None:
    from evergreen.market import Candle
    from evergreen.research.backtest import Costs, Result, run_backtest
    from evergreen.research.experiments.early_stopping import run_early_study
    from evergreen.strategies import Strategy

    output = tmp_path / "study"
    datasets: dict[Path, list[Candle]] = {}

    def read(path: Path, interval: tuple[str, str, str]) -> tuple[list[Candle], str]:
        if path.name.startswith("eval"):
            frozen = json.loads((output / "training-complete.json").read_text())
            assert set(frozen) == {"fixed20", "early"}
            assert all(set(group) == {"mlp-v1", "cnn-v1"} for group in frozen.values())
        if fail_last and path.name == "eval-3":
            raise ValueError("평가 데이터 실패")
        start = datetime.fromisoformat(interval[0]).replace(tzinfo=UTC)
        if path.name == "train-2":
            start = datetime(2024, 11, 25, tzinfo=UTC)
        datasets[path] = [
            Candle(
                open_time=start + timedelta(hours=i),
                open=Decimal(100 + i),
                high=Decimal(101 + i),
                low=Decimal(99 + i),
                close=Decimal(100 + i),
                volume=Decimal(10 + i % 7),
                quote_volume=Decimal(1000),
                fetched_at=start + timedelta(days=20),
            )
            for i in range(240)
        ]
        return datasets[path], "a" * 64

    def comparison(
        dataset: Path,
        start: datetime,
        capital: Decimal,
        costs: Costs,
        output: Path,
        *,
        strategies: tuple[Strategy, ...],
        predictions: dict[Strategy, dict[datetime, Decimal]],
    ) -> list[Result]:
        assert len(strategies) == 9
        return [
            run_backtest(
                datasets[dataset],
                start,
                capital,
                costs,
                strategy,
                predictions=predictions.get(strategy),
            )
            for _ in range(4)
            for strategy in strategies
        ]

    monkeypatch.setattr("evergreen.research.experiments.early_stopping._read", read)
    monkeypatch.setattr("evergreen.research.experiments.early_stopping.compare", comparison)
    training = [Path(f"train-{i}") for i in range(3)]
    evaluation = [Path(f"eval-{i}") for i in range(4)]
    if fail_last:
        with pytest.raises(ValueError, match="평가 데이터 실패"):
            run_early_study(training, evaluation, output)
        assert not (output / "status.json").exists()
        failure = json.loads((output / "failure.json").read_text())
        assert all(len(windows) == 3 for windows in failure["completed"].values())
    else:
        run_early_study(training, evaluation, output)
        status = json.loads((output / "status.json").read_text())
        assert status["comparison_completed"]
        assert not any(
            status[key]
            for key in ("live_enabled", "profitability_validated", "new_holdout_evaluated")
        )
        assert "수익성 최종 검증" in (output / "summary.md").read_text()
    split = json.loads((output / "split.json").read_text())
    assert split["last_training_label"] < split["first_validation_signal"]
    assert split["purged_samples"] == 12
