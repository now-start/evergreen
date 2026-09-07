import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from evergreen.market import Candle
from evergreen.research.backtest import Costs, Result, run_backtest
from evergreen.strategies import META_COMPONENTS, Strategy


def bars(count: int = 210, step: str = ".1") -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    prices = [Decimal(100) + i * Decimal(step) for i in range(count)]
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=p,
            high=p,
            low=p,
            close=p,
            volume=Decimal(10),
            quote_volume=Decimal(1000),
            fetched_at=start + timedelta(days=100),
        )
        for i, p in enumerate(prices)
    ]


def costs(fee: str = ".0005") -> Costs:
    return Costs(
        buy_fee=Decimal(fee),
        sell_fee=Decimal(fee),
        buy_slippage=Decimal(".001"),
        sell_slippage=Decimal(".001"),
        min_notional=Decimal(5000),
        quantity_step=Decimal(".00000001"),
    )


def test_meta_holds_until_rule_or_time_exit_not_probability() -> None:
    data = bars()
    scores = {b.close_time: Decimal(0) for b in data[168:]}
    scores[data[168].close_time] = Decimal(".5")
    result = run_backtest(
        data,
        data[169].open_time,
        Decimal(1000000),
        costs(),
        "meta-mlp-trend-v1",
        predictions=scores,
    )
    assert len(result.fills) == 2
    buy, sell = result.fills
    assert buy.time == data[169].open_time
    assert sell.time - buy.time == timedelta(hours=12)
    assert sell.reason == "signal"
    approved = {b.close_time: Decimal(1) for b in data[168:]}
    filtered = run_backtest(
        data,
        data[169].open_time,
        Decimal(1000000),
        costs(),
        "meta-mlp-trend-v1",
        predictions=approved,
    )
    control = run_backtest(data, data[169].open_time, Decimal(1000000), costs(), "trend-12h-v1")
    assert filtered.fills == control.fills
    assert filtered.net_return == control.net_return


def test_event_labels_include_costs_and_only_causal_features() -> None:
    import torch

    from evergreen.research.learning.meta import event_samples

    data = bars()
    low = event_samples(data, "sma-slow-v1", costs())
    high = event_samples(data, "sma-slow-v1", costs(".05"))
    assert len(low.labels) > 0
    assert torch.equal(low.features, high.features)
    assert low.labels.sum() > high.labels.sum()
    assert low.signal_times[0] == data[167].close_time
    assert low.label_times[-1] == data[-1].close_time
    partial = event_samples(data[:-10], "sma-slow-v1", costs())
    assert torch.equal(partial.features, low.features[: len(partial.labels)])
    assert torch.equal(partial.labels, low.labels[: len(partial.labels)])
    assert all(
        t - s == timedelta(hours=13) for s, t in zip(low.signal_times, low.label_times, strict=True)
    )


def test_event_insufficient_input_and_unknown_rule() -> None:
    from evergreen.research.learning.meta import event_samples

    assert len(event_samples(bars(170), "sma-slow-v1", costs()).labels) == 0
    with pytest.raises(ValueError):
        event_samples(bars(), "cash", costs())


def test_meta_training_gate_requires_both_classes_and_enough_samples() -> None:
    from evergreen.research.experiments.meta import training_eligible

    assert training_eligible(
        {"samples": 50, "positives": 25, "negatives": 25},
        {"samples": 20, "positives": 10, "negatives": 10},
    )
    assert not training_eligible(
        {"samples": 49, "positives": 25, "negatives": 24},
        {"samples": 20, "positives": 10, "negatives": 10},
    )
    assert not training_eligible(
        {"samples": 50, "positives": 50, "negatives": 0},
        {"samples": 20, "positives": 10, "negatives": 10},
    )
    assert not training_eligible(
        {"samples": 50, "positives": 25, "negatives": 25},
        {"samples": 19, "positives": 10, "negatives": 9},
    )


@pytest.mark.parametrize("failed_test", [False, True])
@pytest.mark.parametrize("fit_models", [False, True])
def test_meta_pipeline_freezes_models_then_selection_before_observed_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failed_test: bool, fit_models: bool
) -> None:
    import torch

    from evergreen.research.experiments.meta import run_meta_study
    from evergreen.research.learning.models import Samples, train_model

    output = tmp_path / "study"
    datasets: dict[Path, list[Candle]] = {}
    expected_models = set(META_COMPONENTS) if fit_models else set()
    expected_selected = "meta-mlp-trend-v1" if fit_models else "cash"

    def read(path: Path, interval: tuple[str, str, str]) -> tuple[list[Candle], str]:
        if path.name.startswith("select"):
            frozen = json.loads((output / "training-complete.json").read_text())
            assert set(frozen["models"]) == expected_models
        if path.name == "test":
            frozen = json.loads((output / "selection.json").read_text())
            assert not frozen["test_evaluated"] and frozen["selected"] == expected_selected
            if failed_test:
                raise ValueError("후속 평가 품질 실패")
        start = datetime.fromisoformat(interval[0]).replace(tzinfo=UTC)
        shift = start - bars()[0].open_time
        datasets[path] = [
            bar.model_copy(
                update={"open_time": bar.open_time + shift, "fetched_at": bar.fetched_at + shift}
            )
            for bar in bars(240)
        ]
        return datasets[path], "a" * 64

    def samples(candles: list[Candle], rule: Strategy, fees: Costs) -> Samples:
        start = candles[0].open_time
        if start.month == 11:
            start = start.replace(month=12)
        times = tuple(start + timedelta(hours=i) for i in range(60))
        return Samples(
            torch.arange(60 * 32 * 5, dtype=torch.float32).reshape(60, 32, 5) / 100,
            (torch.arange(60) % 2).float(),
            times,
            tuple(t + timedelta(hours=13) for t in times),
        )

    train = samples(bars(), "sma-slow-v1", costs())
    baseline = {
        name: train_model([train], name, tmp_path / name, epochs=2) for name in ("mlp-v1", "cnn-v1")
    }
    monkeypatch.setattr("evergreen.research.experiments.meta.frozen_models", lambda *args: baseline)
    monkeypatch.setattr("evergreen.research.experiments.meta._read", read)
    monkeypatch.setattr("evergreen.research.experiments.meta.event_samples", samples)
    if not fit_models:
        monkeypatch.setattr(
            "evergreen.research.experiments.meta.training_eligible", lambda *args: False
        )
    monkeypatch.setattr(
        "evergreen.research.experiments.meta.choose_meta", lambda *args: expected_selected
    )

    def comparison(
        path: Path,
        start: datetime,
        capital: Decimal,
        fees: Costs,
        target: Path,
        *,
        strategies: tuple[Strategy, ...],
        predictions: dict[Strategy, dict[datetime, Decimal]],
    ) -> list[Result]:
        if path.name == "test":
            assert set(strategies) & META_COMPONENTS.keys() == (
                {expected_selected} if fit_models else set()
            )
        return [
            run_backtest(
                datasets[path],
                start,
                capital,
                fees,
                strategy,
                predictions=predictions.get(strategy),
            )
            for _ in range(4)
            for strategy in strategies
        ]

    monkeypatch.setattr("evergreen.research.experiments.meta.compare", comparison)
    args = (
        [Path(f"train-{i}") for i in range(3)],
        [Path(f"select-{i}") for i in range(3)],
        Path("test"),
        tmp_path / "old",
        output,
    )
    if failed_test:
        with pytest.raises(ValueError, match="품질 실패"):
            run_meta_study(*args)
        assert len(json.loads((output / "failure.json").read_text())["completed_windows"]) == 3
        assert not (output / "status.json").exists()
    else:
        run_meta_study(*args)
        status = json.loads((output / "status.json").read_text())
        assert status["trained_models"] == len(expected_models) and status["comparison_completed"]
        assert not any(
            status[k] for k in ("live_enabled", "profitability_validated", "new_holdout_evaluated")
        )
    for name in expected_models:
        task = json.loads((output / "models" / name / "task.json").read_text())
        assert task["task"] == "candidate_net_profit" and task["rule"] == META_COMPONENTS[name][1]


def test_meta_selection_keeps_risk_gate_and_cash_fallback() -> None:
    from evergreen.research.experiments.meta import choose_meta

    data = bars()
    row = run_backtest(data, data[169].open_time, Decimal(1000000), costs(), "cash")
    windows = {
        f"selection-{i}": [
            row.model_copy(update={"strategy": s, "natural_exits": 1, "net_return": Decimal(".02")})
            for s in META_COMPONENTS
            for _ in range(4)
        ]
        for i in (1, 2, 3)
    }
    assert choose_meta(windows, tuple(META_COMPONENTS)) == "meta-cnn-breakout-v1"
    for results in windows.values():
        for result in results:
            result.halted = True
    assert choose_meta(windows, tuple(META_COMPONENTS)) == "cash"
    assert choose_meta(windows, ()) == "cash"
    with pytest.raises(ValueError):
        choose_meta({}, ())
    with pytest.raises(ValueError):
        choose_meta(windows, ("cash",))


def test_meta_cli_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from evergreen.research.__main__ import main

    def run(train: list[Path], select: list[Path], test: Path, old: Path, output: Path) -> None:
        assert (
            len(train) == len(select) == 3
            and test == Path("test")
            and old == Path("old")
            and output == tmp_path
        )

    monkeypatch.setattr("evergreen.research.experiments.meta.run_meta_study", run)
    assert (
        main(
            [
                "meta-study",
                "--training",
                "a",
                "b",
                "c",
                "--selection",
                "d",
                "e",
                "f",
                "--test",
                "test",
                "--trained-experiment",
                "old",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )


def test_event_group_count_and_risk_exit() -> None:
    from evergreen.research.learning.meta import event_samples, sample_counts

    data = bars()
    for i in range(170, len(data)):
        data[i] = data[i].model_copy(
            update=dict(open=Decimal(50), high=Decimal(50), low=Decimal(50), close=Decimal(50))
        )
    events = event_samples(data, "sma-slow-v1", costs())
    assert events.labels[0].item() == 0
    counts = sample_counts([events])
    assert counts["non_overlapping_groups"] < counts["samples"]
    result = run_backtest(
        data,
        data[169].open_time,
        Decimal(1000000),
        costs(),
        "meta-mlp-trend-v1",
        predictions={b.close_time: Decimal(1) for b in data[168:]},
    )
    assert result.halted and result.fills[-1].reason == "risk"
