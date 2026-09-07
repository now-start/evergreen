from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import torch

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.learning.models import (
    load_model,
    make_samples,
    train_model,
    window_features,
)


def candles(count: int = 100) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal(10 + i % 7),
            quote_volume=Decimal(1000),
            fetched_at=start + timedelta(days=100),
        )
        for i in range(count)
    ]


def test_features_use_only_past_55_bars_and_no_future_values() -> None:
    data = candles()
    windows, times = window_features(data)
    assert windows.shape == (46, 32, 5)
    assert times[0] == data[54].close_time
    shorter, earlier = window_features(data[:80])
    assert torch.equal(windows[: len(shorter)], shorter)
    assert times[: len(earlier)] == earlier
    assert torch.isfinite(windows).all()


def test_labels_are_purged_before_segment_boundary() -> None:
    data = candles()
    samples = make_samples(data)
    assert samples.features.shape == (33, 32, 5)
    assert samples.label_times[-1] == data[-1].open_time
    assert all(label < data[-1].close_time for label in samples.label_times)
    assert all(
        label - signal == timedelta(hours=12)
        for label, signal in zip(samples.label_times, samples.signal_times, strict=True)
    )
    assert torch.equal(samples.labels, torch.ones(33))


def test_short_or_gapped_segments_fail() -> None:
    with pytest.raises(ValueError):
        make_samples(candles(60))
    with pytest.raises(ValueError):
        window_features(candles(54))
    data = candles()
    with pytest.raises(ValueError):
        window_features(data[:60] + data[61:])


@pytest.mark.parametrize("architecture", ["mlp-v1", "cnn-v1"])
def test_training_reload_and_reproducibility(architecture: str, tmp_path: Path) -> None:
    samples = make_samples(candles())
    first = train_model([samples], architecture, tmp_path / "one", epochs=2)
    second = train_model([samples], architecture, tmp_path / "two", epochs=2)
    before = first.predict(candles())
    assert before == second.predict(candles())
    restored = load_model(tmp_path / "one")
    assert before == restored.predict(candles())
    assert all(Decimal(0) <= p <= Decimal(1) for p in before.values())
    # Inference must not fit or update normalization.
    mean = first.mean.clone()
    first.predict(candles(120))
    assert torch.equal(first.mean, mean)
    with pytest.raises(FileExistsError):
        train_model([samples], architecture, tmp_path / "one", epochs=2)


def test_unknown_architecture_fails_before_creating_artifact(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        train_model([make_samples(candles())], "unknown", tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_checkpoint_tampering_is_rejected_before_load(tmp_path: Path) -> None:
    output = tmp_path / "model"
    train_model([make_samples(candles())], "mlp-v1", output, epochs=1)
    checkpoint = output / "checkpoint.pt"
    checkpoint.write_bytes(checkpoint.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="해시"):
        load_model(output)


def test_model_probabilities_drive_existing_execution_and_missing_scores_fail() -> None:
    data = candles(80)
    start = data[60].open_time
    costs = Costs.model_validate(
        {
            "buy_fee": "0",
            "sell_fee": "0",
            "buy_slippage": "0",
            "sell_slippage": "0",
            "min_notional": "1",
            "quantity_step": "0.00000001",
        }
    )
    scores = {bar.close_time: Decimal("0.3") for bar in data[59:]}
    scores[start] = Decimal("0.6")
    run = run_backtest(data, start, Decimal(1000), costs, "mlp-v1", predictions=scores)
    assert len(run.fills) == 2
    assert run.fills[0].price == data[60].open
    assert run.fills[1].price == data[61].open
    delayed = run_backtest(
        data, start, Decimal(1000), costs, "mlp-v1", predictions=scores, extra_delay_bars=1
    )
    assert delayed.fills[0].price == data[61].open
    assert delayed.fills[1].price == data[63].open
    with pytest.raises(ValueError, match="예측"):
        run_backtest(data, start, Decimal(1000), costs, "mlp-v1")
    with pytest.raises(ValueError, match="예측"):
        run_backtest(data, start, Decimal(1000), costs, "mlp-v1", predictions={})
    scores[start] = Decimal("NaN")
    with pytest.raises(ValueError, match="예측"):
        run_backtest(data, start, Decimal(1000), costs, "mlp-v1", predictions=scores)
