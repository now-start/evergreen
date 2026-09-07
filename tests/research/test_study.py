import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from evergreen.market import Candle
from evergreen.research.__main__ import main
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments.rules import (
    REVERSION_WINDOWS,
    WINDOWS,
    assess,
    run_study,
    select_candidate,
)
from evergreen.strategies import Strategy


def result(strategy: Strategy, profit: str = "0.02", **changes: object) -> Result:
    values: dict[str, object] = {
        "strategy": strategy,
        "costs": Costs(
            buy_fee=Decimal(0),
            sell_fee=Decimal(0),
            buy_slippage=Decimal(0),
            sell_slippage=Decimal(0),
            min_notional=Decimal(1),
            quantity_step=Decimal(1),
        ),
        "initial_capital": 1000,
        "start": datetime(2024, 1, 9, tzinfo=UTC),
        "end": datetime(2024, 3, 1, tzinfo=UTC),
        "extra_delay_bars": 0,
        "cash": 1020,
        "btc": 0,
        "final_equity": 1020,
        "net_return": Decimal(profit),
        "max_drawdown": Decimal("0.03"),
        "total_fees": 0,
        "total_slippage": 0,
        "natural_exits": 5,
        "exposure_ratio": 0,
        "halted": False,
        "fills": [],
        "rejections": [],
        "equity_curve": [],
    }
    return Result.model_validate(values | changes)


def test_selection_uses_worst_development_scenario_not_best_return() -> None:
    results = [result("sma-slow-v1", p) for p in ("0.5", "0.4", "-0.1", "0.3")]
    results += [result("breakout-v1", "0.01") for _ in range(4)]
    assert select_candidate(results) == "breakout-v1"


def test_selection_ties_use_drawdown_then_stable_identifier() -> None:
    results = [result(s) for s in ("sma-slow-v1", "breakout-v1") for _ in range(4)]
    assert select_candidate(results) == "breakout-v1"
    results[4].max_drawdown = Decimal("0.04")
    assert select_candidate(results) == "sma-slow-v1"


def test_selection_requires_both_candidates_four_scenarios() -> None:
    with pytest.raises(ValueError, match="4개"):
        select_candidate([result("breakout-v1")])


def test_new_experiment_selects_only_mean_reversion_candidates() -> None:
    candidates: tuple[Strategy, ...] = ("band-reversion-v1", "rsi-rebound-v1")
    rows = [result(strategy, "0.01") for _ in range(4) for strategy in candidates]
    rows += [result("sma-slow-v1", "0.5") for _ in range(4)]
    assert select_candidate(rows, candidates=candidates) == "band-reversion-v1"


def test_new_experiment_must_beat_both_sma_baselines() -> None:
    candidates: tuple[Strategy, ...] = ("band-reversion-v1", "rsi-rebound-v1")
    rows = [
        result("rsi-rebound-v1", "0.02"),
        result("sma-trend-v0", "0.01"),
        result("sma-slow-v1", "0.03"),
    ] * 4
    checks = assess(
        {name: rows for name in WINDOWS},
        "rsi-rebound-v1",
        candidate_ids=candidates,
        comparisons=("sma-trend-v0", "sma-slow-v1"),
    )
    assert not checks["beats_baseline"]
    assert checks["positive"] and checks["risk"] and checks["exit_count"]


def group(profit: str = "0.02", **changes: object) -> list[Result]:
    return [result("sma-trend-v0", "0.01"), result("breakout-v1", profit, **changes)] * 4


def test_assessment_requires_all_windows_and_scenarios() -> None:
    windows = {name: group() for name in ("development", "validation-a", "validation-b")}
    assert all(assess(windows, "breakout-v1").values())
    windows["validation-b"] = group(halted=True)
    assert not assess(windows, "breakout-v1")["risk"]
    windows["validation-b"] = group(profit="0")
    checks = assess(windows, "breakout-v1")
    assert not checks["positive"] and not checks["beats_baseline"]
    windows["validation-b"] = group(natural_exits=4)
    assert not assess(windows, "breakout-v1")["exit_count"]
    windows["validation-b"] = group(max_drawdown="0.10001")
    assert not assess(windows, "breakout-v1")["risk"]


def test_assessment_rejects_missing_window_or_scenario() -> None:
    with pytest.raises(ValueError):
        assess({"development": group()}, "breakout-v1")
    windows = {name: group() for name in ("development", "validation-a", "validation-b")}
    windows["validation-b"] = group()[:-1]
    with pytest.raises(ValueError):
        assess(windows, "breakout-v1")


def mock_study(
    monkeypatch: pytest.MonkeyPatch, output: Path, *, fail: bool = False, experiment: str = "01"
) -> list[str]:
    observed: list[str] = []
    chosen: Strategy = "breakout-v1" if experiment == "01" else "band-reversion-v1"
    windows = WINDOWS if experiment == "01" else REVERSION_WINDOWS

    def load(path: Path) -> tuple[list[Candle], str]:
        name = path.name
        if name != "development":
            selected = json.loads((output / "selection.json").read_text())
            assert selected["selected"] == chosen
            assert selected["validation_evaluated"] is False
        observed.append(name)
        if fail and name == "validation-a":
            raise ValueError("검증 데이터 오류")
        begin, _, end = windows[name]
        dates = [
            datetime.fromisoformat(begin).replace(tzinfo=UTC),
            datetime.fromisoformat(end).replace(tzinfo=UTC) - timedelta(hours=1),
        ]
        return [
            Candle(
                open_time=date,
                open=Decimal(100),
                high=Decimal(100),
                low=Decimal(100),
                close=Decimal(100),
                volume=Decimal(1),
                quote_volume=Decimal(100),
                fetched_at=datetime.now(UTC),
            )
            for date in dates
        ], "a" * 64

    def comparison(
        dataset: Path,
        start: datetime,
        capital: Decimal,
        costs: Costs,
        output: Path,
        *,
        strategies: tuple[Strategy, ...],
    ) -> list[Result]:
        if dataset.name != "development":
            assert ("sma-slow-v1" if experiment == "01" else "rsi-rebound-v1") not in strategies
        return [
            result(strategy, "0.02" if strategy == chosen else "0.01")
            for _ in range(4)
            for strategy in strategies
        ]

    monkeypatch.setattr("evergreen.research.experiments.rules.load_dataset", load)
    monkeypatch.setattr("evergreen.research.experiments.rules.compare", comparison)
    return observed


def test_study_freezes_selection_before_reading_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "study"
    observed = mock_study(monkeypatch, output)
    assert all(run_study({name: Path(name) for name in WINDOWS}, output).values())
    assert observed == list(WINDOWS)
    verdict = json.loads((output / "verdict.json").read_text())
    assert verdict["research_passed"] and not verdict["live_enabled"]
    assert "연구 조건 통과" in (output / "summary.md").read_text()
    with pytest.raises(FileExistsError):
        run_study({name: Path(name) for name in WINDOWS}, output)


def test_study_failure_preserves_selection_and_stops_before_next_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "study"
    observed = mock_study(monkeypatch, output, fail=True)
    with pytest.raises(ValueError, match="검증 데이터 오류"):
        run_study({name: Path(name) for name in WINDOWS}, output)
    assert observed == ["development", "validation-a"]
    failure = json.loads((output / "failure.json").read_text())
    assert failure["completed_windows"] == ["development"]
    assert failure["selected"] == "breakout-v1"
    assert not (output / "verdict.json").exists()


def test_study_rejects_changed_dates_and_missing_datasets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="경로"):
        run_study({}, tmp_path / "missing")
    output = tmp_path / "study"
    mock_study(monkeypatch, output)
    # Supply the validation dataset as development; the frozen protocol must reject it.
    mappings = {name: Path("development") for name in WINDOWS}
    with pytest.raises(ValueError, match="기간"):
        run_study(mappings, output)


def test_study_cli_reports_in_korean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "study"
    mock_study(monkeypatch, output)
    args = ["study", "--output", str(output)]
    for name in WINDOWS:
        args.extend(["--" + name, name])
    assert main(args) == 0
    assert "연구 조건 통과" in capsys.readouterr().out


def test_reversion_study_cli_preserves_its_own_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "study"
    observed = mock_study(monkeypatch, output, experiment="02r")
    args = ["study", "--experiment", "02r", "--output", str(output)]
    for name in REVERSION_WINDOWS:
        args.extend(["--" + name, name])
    assert main(args) == 0
    assert observed == list(REVERSION_WINDOWS)
    protocol = json.loads((output / "protocol.json").read_text())
    assert protocol["experiment"] == "02r"
    assert protocol["windows"]["development"] == ["2025-01-03", "2025-01-11", "2025-03-01"]
    assert protocol["comparison_strategies"] == ["sma-trend-v0", "sma-slow-v1"]
    assert "실험 02r 결과" in (output / "summary.md").read_text()


def test_unknown_study_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "study"
    with pytest.raises(ValueError, match="지원"):
        run_study({name: Path(name) for name in WINDOWS}, output, experiment="unknown")
    assert not output.exists()
