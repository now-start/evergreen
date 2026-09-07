import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.strategies import HYBRID_COMPONENTS, Strategy, hybrid_target, warmup_bars


def bars(count: int = 200) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=Decimal(100 + i),
            high=Decimal(100 + i),
            low=Decimal(100 + i),
            close=Decimal(100 + i),
            volume=Decimal(10),
            quote_volume=Decimal(1000),
            fetched_at=start + timedelta(days=30),
        )
        for i in range(count)
    ]


@pytest.mark.parametrize("strategy", list(HYBRID_COMPONENTS))
def test_hybrid_entry_requires_both_and_exit_either(
    monkeypatch: pytest.MonkeyPatch, strategy: Strategy
) -> None:
    data = bars()
    rule = None
    monkeypatch.setattr("evergreen.strategies.registry.strategy_target", lambda *args: rule)
    assert hybrid_target(data, False, strategy, Decimal("0.9")) is None
    rule = "buy"
    assert hybrid_target(data, False, strategy, Decimal("0.59")) is None
    assert hybrid_target(data, False, strategy, Decimal("0.60")) == "buy"
    rule = None
    assert hybrid_target(data, True, strategy, Decimal("0.46")) is None
    assert hybrid_target(data, True, strategy, Decimal("0.45")) == "sell"
    rule = "sell"
    assert hybrid_target(data, True, strategy, Decimal("0.9")) == "sell"


def test_hybrid_engine_uses_next_open_costs_and_validated_probabilities() -> None:
    data = bars()
    start = data[169].open_time
    costs = Costs(
        buy_fee=Decimal(".0005"),
        sell_fee=Decimal(".0005"),
        buy_slippage=Decimal(".001"),
        sell_slippage=Decimal(".001"),
        min_notional=Decimal(5000),
        quantity_step=Decimal(".00000001"),
    )
    scores = {bar.close_time: Decimal(".9") for bar in data[168:]}
    for strategy in HYBRID_COMPONENTS:
        assert warmup_bars(strategy) >= 168
        with pytest.raises(ValueError, match="예측 확률"):
            run_backtest(data, start, Decimal(1000000), costs, strategy)
    result = run_backtest(data, start, Decimal(1000000), costs, "mlp-trend-v1", predictions=scores)
    assert result.fills[0].signal_time == data[168].close_time
    assert result.fills[0].time == data[169].open_time
    assert result.fills[0].price == data[169].open * Decimal("1.001")
    delayed = run_backtest(
        data, start, Decimal(1000000), costs, "mlp-trend-v1", predictions=scores, extra_delay_bars=1
    )
    assert delayed.fills[0].time == data[170].open_time
    scores[start] = Decimal("NaN")
    with pytest.raises(ValueError, match="예측 확률"):
        run_backtest(data, start, Decimal(1000000), costs, "mlp-trend-v1", predictions=scores)


def test_selection_rejects_inactive_and_risky_candidates() -> None:
    from evergreen.research.backtest import Result
    from evergreen.research.experiments.hybrid import choose_hybrid

    row = Result(
        strategy="cash",
        costs=Costs(
            buy_fee=Decimal(".0005"),
            sell_fee=Decimal(".0005"),
            buy_slippage=Decimal(".001"),
            sell_slippage=Decimal(".001"),
            min_notional=Decimal(5000),
            quantity_step=Decimal(".00000001"),
        ),
        initial_capital=Decimal(1000000),
        start=bars()[169].open_time,
        end=bars()[-1].close_time,
        extra_delay_bars=0,
        cash=Decimal(1000000),
        btc=Decimal(0),
        final_equity=Decimal(1000000),
        net_return=Decimal(0),
        max_drawdown=Decimal(0),
        total_fees=Decimal(0),
        total_slippage=Decimal(0),
        natural_exits=0,
        exposure_ratio=Decimal(0),
        halted=False,
        fills=[],
        rejections=[],
        equity_curve=[],
    )
    windows = {
        f"selection-{i}": [
            row.model_copy(update={"strategy": strategy})
            for strategy in HYBRID_COMPONENTS
            for _ in range(4)
        ]
        for i in range(1, 4)
    }
    assert choose_hybrid(windows) == "cash"
    for rows in windows.values():
        for result in rows:
            result.natural_exits = 1
            result.net_return = Decimal(".02")
            if result.strategy == "mlp-trend-v1":
                result.net_return = Decimal(".50")
                result.max_drawdown = Decimal(".11")
            if result.strategy == "cnn-trend-v1":
                result.halted = True
    assert choose_hybrid(windows) == "cnn-breakout-v1"
    with pytest.raises(ValueError):
        choose_hybrid({})


@pytest.mark.parametrize("fail_last", [False, True])
def test_hybrid_pipeline_freezes_selection_and_preserves_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_last: bool
) -> None:
    from evergreen.research.backtest import Result
    from evergreen.research.experiments.hybrid import frozen_models, run_hybrid_study
    from evergreen.research.learning.models import make_samples, train_model

    origin = tmp_path / "origin"
    hashes = {}
    train = make_samples(bars(100))
    val_bars = [
        bar.model_copy(
            update={
                "open_time": bar.open_time + timedelta(days=335),
                "fetched_at": bar.fetched_at + timedelta(days=335),
            }
        )
        for bar in bars(100)
    ]
    for name in ("mlp-v1", "cnn-v1"):
        fitted = train_model(
            [train],
            name,
            origin / "models" / "early" / name,
            epochs=2,
            validation=[make_samples(val_bars)],
            patience=10,
        )
        hashes[name] = fitted.metadata.checkpoint_sha256
    (origin / "split.json").write_text(
        json.dumps(
            {
                "last_training_label": "2024-11-30T23:00:00+00:00",
                "first_validation_signal": "2024-12-01T00:00:00+00:00",
            }
        )
    )
    (origin / "protocol.json").write_text("{}")
    (origin / "training-complete.json").write_text(json.dumps({"early": hashes}))
    output = tmp_path / "study"
    datasets: dict[Path, list[Candle]] = {}

    def read(path: Path, interval: tuple[str, str, str]) -> tuple[list[Candle], str]:
        if path.name.startswith("test"):
            frozen = json.loads((output / "selection.json").read_text())
            assert frozen["selected"] == "mlp-trend-v1"
            assert not frozen["test_evaluated"]
            assert frozen["models"] == hashes
        if path.name == "test-new" and fail_last:
            raise ValueError("새 구간 품질 실패")
        begin = datetime.fromisoformat(interval[0]).replace(tzinfo=UTC)
        shift = begin - bars()[0].open_time
        datasets[path] = [
            bar.model_copy(
                update={
                    "open_time": bar.open_time + shift,
                    "fetched_at": bar.fetched_at + shift,
                }
            )
            for bar in bars(240)
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
            assert set(strategies) & HYBRID_COMPONENTS.keys() == {"mlp-trend-v1"}
        for strategy in strategies:
            if strategy in HYBRID_COMPONENTS:
                assert predictions[strategy] == predictions[HYBRID_COMPONENTS[strategy][0]]
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

    monkeypatch.setattr("evergreen.research.experiments.hybrid._read", read)
    monkeypatch.setattr("evergreen.research.experiments.hybrid.compare", comparison)
    # Ranking itself is tested separately; exercise non-cash downstream wiring here.
    monkeypatch.setattr(
        "evergreen.research.experiments.hybrid.choose_hybrid", lambda windows: "mlp-trend-v1"
    )
    args = (
        origin,
        [Path(f"select-{i}") for i in range(3)],
        [Path("test-observed"), Path("test-new")],
        output,
    )
    if fail_last:
        with pytest.raises(ValueError, match="품질 실패"):
            run_hybrid_study(*args)
        assert not (output / "status.json").exists()
        assert len(json.loads((output / "failure.json").read_text())["completed_windows"]) == 4
        assert "검증 미완료" in (output / "summary.md").read_text()
    else:
        run_hybrid_study(*args)
        status = json.loads((output / "status.json").read_text())
        assert status["comparison_completed"] and status["new_window_evaluated"]
        assert not status["live_enabled"] and not status["profitability_validated"]
    for name, digest in hashes.items():
        assert (
            json.loads((output / "models" / name / "model.json").read_text())["checkpoint_sha256"]
            == digest
        )
    hashes["mlp-v1"] = "0" * 64
    (origin / "training-complete.json").write_text(json.dumps({"early": hashes}))
    with pytest.raises(ValueError, match="고정된"):
        frozen_models(origin, tmp_path / "wrong")


def test_hybrid_cli_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from evergreen.research.__main__ import main

    def run(origin: Path, selection: list[Path], tests: list[Path], output: Path) -> None:
        assert origin == Path("origin") and len(selection) == 3 and len(tests) == 2
        assert output == tmp_path

    monkeypatch.setattr("evergreen.research.experiments.hybrid.run_hybrid_study", run)
    assert (
        main(
            [
                "hybrid-study",
                "--trained-experiment",
                "origin",
                "--selection",
                "a",
                "b",
                "c",
                "--tests",
                "d",
                "e",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )


def test_hybrid_cannot_override_risk_stop() -> None:
    data = bars()
    costs = Costs(
        buy_fee=Decimal(".0005"),
        sell_fee=Decimal(".0005"),
        buy_slippage=Decimal(".001"),
        sell_slippage=Decimal(".001"),
        min_notional=Decimal(5000),
        quantity_step=Decimal(".00000001"),
    )
    for i in range(170, len(data)):
        data[i] = data[i].model_copy(
            update=dict(open=Decimal(100), high=Decimal(100), low=Decimal(100), close=Decimal(100))
        )
    scores = {bar.close_time: Decimal(".99") for bar in data[168:]}
    result = run_backtest(
        data, data[169].open_time, Decimal(1000000), costs, "mlp-trend-v1", predictions=scores
    )
    assert result.halted
    assert any(fill.reason == "risk" and fill.side == "sell" for fill in result.fills)
    assert sum(fill.side == "buy" for fill in result.fills) == 1


@pytest.mark.parametrize(
    "split",
    [
        {},
        [],
        {
            "last_training_label": "2024-11-30T23:00:00",
            "first_validation_signal": "2024-12-01T00:00:00",
        },
    ],
)
def test_invalid_origin_is_reported_as_cli_failure(tmp_path: Path, split: object) -> None:
    from evergreen.research.__main__ import main

    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "split.json").write_text(json.dumps(split))
    output = tmp_path / "out"
    assert (
        main(
            [
                "hybrid-study",
                "--trained-experiment",
                str(origin),
                "--selection",
                "a",
                "b",
                "c",
                "--tests",
                "d",
                "e",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert (output / "failure.json").exists()
    assert "검증 미완료" in (output / "summary.md").read_text()
    assert not (output / "status.json").exists()


@pytest.mark.parametrize("frozen", [{}, [], {"early": {}}])
def test_invalid_frozen_manifest_is_rejected(tmp_path: Path, frozen: object) -> None:
    from evergreen.research.experiments.hybrid import frozen_models

    (tmp_path / "split.json").write_text(
        json.dumps(
            {
                "last_training_label": "2024-11-30T23:00:00+00:00",
                "first_validation_signal": "2024-12-01T00:00:00+00:00",
            }
        )
    )
    (tmp_path / "training-complete.json").write_text(json.dumps(frozen))
    with pytest.raises(ValueError):
        frozen_models(tmp_path, tmp_path / "out")
