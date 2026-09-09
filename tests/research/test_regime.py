import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
import torch
from test_learning import candles

from evergreen.research.learning.regime import make_regime_samples, split_samples
from evergreen.strategies.regime import RegimeRouter, stabilize


def test_regime_labels_use_future_only_as_targets() -> None:
    data = candles(180)
    samples = make_regime_samples(data)
    assert set(samples.labels.tolist()) <= {0, 1, 2}
    assert all(
        b - a == timedelta(hours=24)
        for a, b in zip(samples.signal_times, samples.label_times, strict=True)
    )
    changed = data.copy()
    changed[-1] = changed[-1].model_copy(update={"open": Decimal(1), "low": Decimal(1)})
    other = make_regime_samples(changed)
    assert torch.equal(samples.features, other.features)
    assert samples.labels[-1] != other.labels[-1]


def test_purge_training_and_validation_label_boundaries() -> None:
    samples = make_regime_samples(candles(250))
    boundary = samples.signal_times[80]
    end = samples.signal_times[160]
    train = split_samples(samples, samples.signal_times[0], boundary)
    validation = split_samples(samples, boundary, end)
    assert max(train.label_times) < min(validation.signal_times)
    assert max(validation.label_times) < end
    with pytest.raises(ValueError, match="표본"):
        split_samples(samples, end, end)


def test_switch_confirmation_cooldown_and_unknown() -> None:
    times = [bar.close_time for bar in candles(15)]
    values = dict(zip(times, [2] * 3 + [0] * 6 + [1] * 6, strict=True))
    result = stabilize(values)
    assert [result[t] for t in times[:3]] == [-1, -1, 2]
    assert result[times[7]] == 2
    assert result[times[8]] == 0
    assert result[times[14]] == 1


def test_router_keeps_entry_exit_rule_and_bear_overrides() -> None:
    data = candles(180)
    router = RegimeRouter()
    assert router.target(data, False, -1) is None
    assert router.target(data, False, 0) is None
    # Rising fixture exceeds the previous high by more than the entry buffer.
    latest = data[-1]
    data[-1] = latest.model_copy(update={"close": latest.high + 5, "high": latest.high + 5})
    assert router.target(data, False, 2) == "buy"
    assert router.owner == 2
    assert router.target(data, True, 1) is None
    assert router.owner == 2
    assert router.target(data, True, 0) == "sell"


def test_regime_training_is_reproducible_and_restores_best_epoch(tmp_path: Path) -> None:
    from evergreen.research.learning.models import Samples
    from evergreen.research.learning.regime import classification, train_regime

    torch.manual_seed(1)
    x = torch.randn(90, 32, 5)
    y = torch.tensor([0, 1, 2] * 30)
    times = tuple(b.close_time for b in candles(200))
    training = Samples(x[:60], y[:60], times[:60], times[24:84])
    validation = Samples(x[60:], y[60:], times[100:130], times[124:154])
    first = train_regime([training], validation, tmp_path / "first", epochs=3, patience=1)
    second = train_regime([training], validation, tmp_path / "second", epochs=3, patience=1)
    assert torch.equal(first.probabilities(x), second.probabilities(x))
    metadata = json.loads((tmp_path / "first" / "training.json").read_text())
    best = min(metadata["history"], key=lambda row: row["validation_loss"])
    assert metadata["best_epoch"] == best["epoch"]
    before = first.mean.clone()
    predicted, probabilities = first.predict(candles(100))
    assert len(predicted) == len(probabilities)
    assert set(predicted.values()) <= {-1, 0, 1, 2}
    assert torch.equal(before, first.mean)
    assert classification(y, torch.nn.functional.one_hot(y, 3).float(), 1)["macro_f1"] == 1
    with pytest.raises(ValueError, match="경계"):
        train_regime([training], training, tmp_path / "bad")
    with pytest.raises(ValueError, match="세 장세"):
        train_regime(
            [Samples(x[:60], torch.ones(60).long(), times[:60], times[24:84])],
            validation,
            tmp_path / "missing",
        )
    with pytest.raises(ValueError, match="설정"):
        train_regime([], validation, tmp_path / "empty")
    with pytest.raises(FileExistsError):
        train_regime([training], validation, tmp_path / "first", epochs=1)
    with pytest.raises(ValueError):
        make_regime_samples(candles(60))


def test_backtest_requires_exact_regime_timestamps_and_charges_switches() -> None:
    from evergreen.research.backtest import Costs, run_backtest

    data = candles(220)
    # Allow entry at the first evaluation bar, bearish forced exit three hours later.
    data[168] = data[168].model_copy(update={"close": Decimal(290), "high": Decimal(290)})
    regimes = {bar.close_time: 2 if i < 3 else 0 for i, bar in enumerate(data[168:])}
    costs = Costs(
        buy_fee=Decimal(".0005"),
        sell_fee=Decimal(".0005"),
        buy_slippage=Decimal(".001"),
        sell_slippage=Decimal(".001"),
        min_notional=Decimal(1),
        quantity_step=Decimal(".00000001"),
    )
    result = run_backtest(
        data, data[169].open_time, Decimal(1000000), costs, "regime-rules-v1", regimes=regimes
    )
    assert [fill.side for fill in result.fills[:2]] == ["buy", "sell"]
    assert result.fills[0].time == data[169].open_time
    assert result.fills[1].time == data[172].open_time
    assert result.total_fees > 0 and result.total_slippage > 0
    invalid_maps: list[dict[datetime, int] | None] = [None, {}, regimes | {data[0].open_time: 0}]
    for invalid in invalid_maps:
        with pytest.raises(ValueError, match="장세"):
            run_backtest(
                data, data[169].open_time, Decimal(1000000), costs, "regime-mlp-v1", regimes=invalid
            )
    with pytest.raises(ValueError, match="장세"):
        run_backtest(
            data, data[169].open_time, Decimal(1000000), costs, "breakout-v1", regimes=regimes
        )


def test_join_preserves_boundaries_and_never_bridges_gaps() -> None:
    from evergreen.research.experiments.regime import Evaluation, joined_evaluations

    data = candles(700)
    first = Evaluation(
        data[169].open_time, data[:350], {bar.close_time: 2 for bar in data[168:350]}
    )
    second = Evaluation(
        data[350].open_time, data[181:500], {bar.close_time: 0 for bar in data[349:500]}
    )
    third = Evaluation(data[501].open_time, data[332:], {bar.close_time: 1 for bar in data[500:]})
    joined = joined_evaluations([first, second, third])
    assert len(joined) == 2
    assert joined[0].bars == data[:500]
    assert joined[0].predictions[data[350].open_time] == 0
    assert len(first.bars) == 350  # Inputs are not mutated.
    second.bars[0] = second.bars[0].model_copy(update={"volume": Decimal(999)})
    with pytest.raises(ValueError, match="가격"):
        joined_evaluations([first, second])


def test_stabilization_is_prefix_causal_and_gap_resets() -> None:
    times = [b.close_time for b in candles(15)]
    values = {t: 2 for t in times}
    all_values = stabilize(values)
    assert stabilize(dict(list(values.items())[:8])) == {t: all_values[t] for t in times[:8]}
    del values[times[8]]
    assert stabilize(values)[times[9]] == -1
    values[times[-1]] = 7
    with pytest.raises(ValueError):
        stabilize(values)
    with pytest.raises(ValueError):
        stabilize({datetime(2024, 1, 1): 2})  # noqa: DTZ001


def test_study_smoke_and_failure_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import math

    from evergreen.market import Candle, write_json
    from evergreen.research.__main__ import main
    from evergreen.research.experiments import regime
    from evergreen.research.learning.models import Samples
    from evergreen.research.learning.regime import TrainedRegime, train_regime

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
        assert (output.parent / "protocol.json").exists()
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
        feature_set: str,
    ) -> TrainedRegime:
        assert feature_set == "short"
        return train_regime(
            samples, validation, output, epochs=2, patience=1, balanced=balanced, seed=seed
        )

    monkeypatch.setattr(regime, "quarters", lambda: list(pairwise(dates)))
    monkeypatch.setattr(regime, "contiguous_blocks", data)
    monkeypatch.setattr(regime, "train_regime", training)
    output = tmp_path / "study"
    assert main(["regime-study", "--source", "unused", "--output", str(output)]) == 0
    status = json.loads((output / "status.json").read_text())
    assert status["folds"] == 1 and status["live_enabled"] is False
    assert status["checks"]["complete_coverage"] is False
    results = json.loads((output / "results.json").read_text())
    assert len(results["continuous_blocks"][0]) == 24
    assert "누적 수익 아님" in (output / "summary.md").read_text()

    def fail(source: Path, output: Path) -> list[list[Candle]]:
        raise ValueError("quality failure")

    monkeypatch.setattr(regime, "contiguous_blocks", fail)
    failed = tmp_path / "failed"
    assert main(["regime-study", "--source", "unused", "--output", str(failed)]) == 1
    assert (failed / "failure.json").exists() and not (failed / "status.json").exists()
