import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from evergreen.market import Candle
from evergreen.research.backtest import Costs, Result, run_backtest
from evergreen.research.experiments.deep import _read, check_results, choose_model, run_deep_study
from evergreen.strategies import ML_STRATEGIES, Strategy


@pytest.mark.parametrize("fail_last", [False, True])
def test_deep_pipeline_trains_freezes_then_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_last: bool
) -> None:
    output = tmp_path / "study"
    datasets: dict[Path, list[Candle]] = {}

    def read(path: Path, interval: tuple[str, str, str]) -> tuple[list[Candle], str]:
        if path.name.startswith("test"):
            selection = json.loads((output / "selection.json").read_text())
            assert selection["selected"] in ML_STRATEGIES
            assert not selection["test_evaluated"]
        if path.name == "test-b" and fail_last:
            raise ValueError("검증 B 품질 실패")
        start = datetime.fromisoformat(interval[0]).replace(tzinfo=UTC)
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
        if dataset.name.startswith("test"):
            selected = json.loads((output.parent / "selection.json").read_text())["selected"]
            assert list(predictions) == [selected]
            assert len(set(strategies) & set(ML_STRATEGIES)) == 1
        # Exercise the real signal/portfolio engine; cost multipliers are tested in compare().
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

    monkeypatch.setattr("evergreen.research.experiments.deep._read", read)
    monkeypatch.setattr("evergreen.research.experiments.deep.compare", comparison)
    training = [Path(f"train-{i}") for i in range(3)]
    selection = [Path(f"select-{i}") for i in range(3)]
    tests = [Path("test-a"), Path("test-b")]
    if fail_last:
        with pytest.raises(ValueError, match="품질 실패"):
            run_deep_study(training, selection, tests, output)
        assert not (output / "verdict.json").exists()
        failure = json.loads((output / "failure.json").read_text())
        assert failure["completed_windows"] == [
            "selection-1",
            "selection-2",
            "selection-3",
            "test-a",
        ]
        assert "검증 미완료" in (output / "summary.md").read_text()
    else:
        run_deep_study(training, selection, tests, output)
        verdict = json.loads((output / "verdict.json").read_text())
        assert verdict["complete"] and not verdict["live_enabled"]
    for model in ML_STRATEGIES:
        assert (output / "models" / model / "checkpoint.pt").exists()
        metadata = json.loads((output / "models" / model / "model.json").read_text())
        assert metadata["epochs"] == 20
        assert metadata["training_samples"] == 3 * (240 - 67)


def test_deep_selection_cannot_run_on_incomplete_or_final_windows(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        choose_model({})
    with pytest.raises(ValueError):
        check_results({}, "cnn-v1")
    with pytest.raises(ValueError):
        run_deep_study([], [], [], tmp_path / "none")
    assert not (tmp_path / "none").exists()


def test_failed_quality_is_reported_before_missing_clean_file(tmp_path: Path) -> None:
    (tmp_path / "quality.json").write_text('{"status":"failed"}')
    with pytest.raises(ValueError, match="품질 검사"):
        _read(tmp_path, ("2026-04-05", "2026-04-13", "2026-06-01"))


def test_deep_cli_dispatch_without_server_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from evergreen.research.__main__ import main

    def run(
        training: list[Path], selection: list[Path], tests: list[Path], output: Path
    ) -> dict[str, bool]:
        assert len(training) == len(selection) == 3 and len(tests) == 2
        assert output == tmp_path
        return {"positive": False}

    monkeypatch.setattr("evergreen.research.experiments.deep.run_deep_study", run)
    assert (
        main(
            [
                "deep-study",
                "--training",
                "a",
                "b",
                "c",
                "--selection",
                "d",
                "e",
                "f",
                "--tests",
                "g",
                "h",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "딥러닝 수익성 미검증" in capsys.readouterr().out
