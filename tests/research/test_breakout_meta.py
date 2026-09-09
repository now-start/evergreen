import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Literal

import pytest
import torch
from test_learning import candles

from evergreen.market import Candle, write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_meta as study
from evergreen.research.learning.breakout_meta import breakout_events, eligible, payoff_threshold
from evergreen.research.learning.models import (
    FeatureSet,
    Samples,
    TrainedModel,
    make_samples,
    train_model,
    window_features,
)
from evergreen.research.learning.regime import sample_identity, split_samples
from evergreen.strategies import Strategy


def event_bars() -> list[Candle]:
    data = candles(260)
    return [
        b.model_copy(update={"open": p, "high": p, "low": p, "close": p})
        for i, b in enumerate(data)
        for p in [Decimal(100) + Decimal(".2") * i if i < 230 else Decimal(136)]
    ]


def test_actual_exit_costs_censoring_and_temporal_purge() -> None:
    data = event_bars()
    samples, records = breakout_events(data, study.costs())
    assert records and samples.labels[0] == 1
    assert samples.signal_times[0] == data[168].close_time
    assert samples.label_times[0] == data[231].close_time
    assert samples.label_times[0] - samples.signal_times[0] > timedelta(hours=12)
    assert records[0]["exit_reason"] == "signal"
    scored = {b.close_time: 0 for b in data[168:]}
    scored[data[168].close_time] = 2
    actual = run_backtest(
        data,
        data[169].open_time,
        Decimal(1000000),
        study.costs(),
        "regime-mlp-v1",
        regimes=scored,
        regime_policy="breakout-filter",
    )
    assert records[0]["net_return"] == str(actual.net_return)
    expensive, _ = breakout_events(data, study.costs(fee=100))
    assert expensive.labels[0] == 0
    assert torch.equal(samples.features, expensive.features)
    prefix, earlier = breakout_events(data[:240], study.costs())
    assert torch.equal(prefix.features, samples.features) and earlier == records
    censored, rows = breakout_events(data[:220], study.costs())
    assert len(censored.labels) == 0 and rows
    assert all(r["label"] is None and r["status"] == "censored_at_gap_or_end" for r in rows)
    with pytest.raises(ValueError, match="표본"):
        split_samples(samples, data[168].close_time, samples.label_times[0])
    purged = split_samples(
        samples, data[168].close_time, samples.label_times[0] + timedelta(hours=1)
    )
    assert max(purged.label_times) < samples.label_times[0] + timedelta(hours=1)


def test_risk_exit_is_a_completed_label() -> None:
    data = event_bars()
    for i in range(200, len(data)):
        data[i] = data[i].model_copy(
            update={
                "open": Decimal(100),
                "high": Decimal(100),
                "low": Decimal(100),
                "close": Decimal(100),
            }
        )
    samples, records = breakout_events(data, study.costs())
    assert records[0]["exit_reason"] == "risk" and samples.labels[0] == 0
    assert samples.label_times[0] == data[201].close_time


def test_gate_requires_disjoint_events_and_classes() -> None:
    training = {"samples": 100, "positives": 50, "negatives": 50, "non_overlapping_groups": 20}
    validation = {"samples": 20, "positives": 10, "negatives": 10, "non_overlapping_groups": 5}
    assert eligible(training, validation)
    assert not eligible(training, {**validation, "non_overlapping_groups": 4})
    assert not eligible({**training, "non_overlapping_groups": 19}, validation)
    assert not eligible(training, {**validation, "positives": 0})


def test_binary_training_seed_is_reproducible(tmp_path: Path) -> None:
    samples = make_samples(candles(220))
    samples = Samples(
        samples.features,
        torch.tensor([i % 2 for i in range(len(samples.labels))], dtype=torch.float32),
        samples.signal_times,
        samples.label_times,
    )
    models = [
        train_model([samples], "mlp-v1", tmp_path / str(i), epochs=1, seed=seed)
        for i, seed in enumerate((17, 29, 29))
    ]
    assert not torch.equal(
        models[0].probabilities(samples.features), models[1].probabilities(samples.features)
    )
    assert torch.equal(
        models[1].probabilities(samples.features), models[2].probabilities(samples.features)
    )
    assert models[1].metadata.seed == 29
    with pytest.raises(ValueError, match="시드"):
        train_model([samples], "mlp-v1", tmp_path / "bad", seed=-1)


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize(
    "validation_quarters,threshold_mode,loss_weighting,model_family,feature_set,overlap_adjusted",
    [
        (1, "fixed", "uniform", "neural", "short", False),
        (2, "fixed", "uniform", "neural", "short", False),
        (2, "payoff", "uniform", "neural", "short", False),
        (2, "fixed", "absolute-return", "neural", "short", False),
        (2, "fixed", "uniform", "linear", "short", False),
        (2, "payoff", "uniform", "linear", "short", False),
        (2, "fixed", "absolute-return", "linear", "short", False),
        (2, "fixed", "absolute-return", "neural", "short-200", False),
        (2, "fixed", "absolute-return", "neural", "breakout-context", False),
        (2, "fixed", "absolute-return", "neural", "short-200", True),
    ],
)
def test_pipeline_preserves_untrainable_folds_and_real_model_backtests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow: bool,
    validation_quarters: int,
    threshold_mode: Literal["fixed", "payoff"],
    loss_weighting: Literal["uniform", "absolute-return"],
    model_family: Literal["neural", "linear"],
    feature_set: FeatureSet,
    overlap_adjusted: bool,
) -> None:
    dates = [datetime(2020 + i // 4, i % 4 * 3 + 1, 1, tzinfo=UTC) for i in range(10)]
    blocks = []
    for date in (dates[5], dates[7], dates[8]):
        data = candles(350)
        offset = date - data[0].open_time
        blocks.append(
            [
                b.model_copy(
                    update={
                        "open_time": b.open_time + offset,
                        "fetched_at": datetime(2025, 1, 1, tzinfo=UTC),
                    }
                )
                for b in data
            ]
        )

    def load_blocks(source: Path, output: Path) -> list[list[Candle]]:
        assert (output.parent / "protocol.json").exists()
        output.mkdir()
        write_json(output / "coverage.json", {"raw": "fixture"})
        return blocks

    def events(
        bars: list[Candle], costs: object, feature_set: FeatureSet
    ) -> tuple[Samples, list[dict[str, object]]]:
        s = make_samples(bars)
        if feature_set != "short":
            x, times = window_features(bars, feature_set)
            s = Samples(x, torch.zeros(len(x)), times, tuple(t + timedelta(hours=1) for t in times))
        sample = Samples(
            s.features,
            torch.tensor([i % 2 for i in range(len(s.labels))], dtype=torch.float32),
            s.signal_times,
            s.label_times,
        )
        records: list[dict[str, object]] = [
            {
                "signal_time": t.isoformat(),
                "label_time": end.isoformat(),
                "label": int(y),
                "net_return": ".10" if y else "-.02",
                "status": "completed",
            }
            for t, end, y in zip(
                sample.signal_times, sample.label_times, sample.labels.tolist(), strict=True
            )
        ]
        return sample, records

    def training(
        segments: list[Samples],
        architecture: Strategy,
        output: Path,
        *,
        epochs: int,
        validation: list[Samples],
        patience: int,
        seed: int,
        sample_weights: torch.Tensor | None = None,
        validation_weights: torch.Tensor | None = None,
        feature_set: FeatureSet = "short",
    ) -> TrainedModel:
        assert max(t for s in segments for t in s.label_times) < min(
            t for s in validation for t in s.signal_times
        )
        return train_model(
            segments,
            architecture,
            output,
            epochs=2,
            validation=validation,
            patience=1,
            seed=seed,
            sample_weights=sample_weights,
            validation_weights=validation_weights,
            feature_set=feature_set,
        )

    monkeypatch.setattr(study, "quarters", lambda: list(pairwise(dates)))
    monkeypatch.setattr(study, "contiguous_blocks", load_blocks)
    monkeypatch.setattr(study, "breakout_events", events)
    monkeypatch.setattr(study, "eligible", lambda *args: allow)
    monkeypatch.setattr(study, "train_model", training)
    out = tmp_path / "study"
    study.run_study(
        tmp_path,
        out,
        validation_quarters=validation_quarters,
        threshold_mode=threshold_mode,
        loss_weighting=loss_weighting,
        model_family=model_family,
        feature_set=feature_set,
        overlap_adjusted=overlap_adjusted,
    )
    protocol = json.loads((out / "protocol.json").read_text())
    assert protocol["validation_quarters"] == validation_quarters
    assert protocol["feature_set"] == feature_set
    assert protocol["overlap_adjusted"] == overlap_adjusted
    if overlap_adjusted:
        assert protocol["experiment"] == "22"
    assert protocol["warmup"] == (169 if feature_set == "short" else 200)
    assert protocol["training_quarters"] == 8 - validation_quarters
    assert protocol["threshold_mode"] == (
        "weighted-score" if loss_weighting == "absolute-return" else threshold_mode
    )
    if allow and loss_weighting == "absolute-return":
        audit = json.loads((out / "seed-17/audits.json").read_text())[0]
        assert audit["training_sha256"] == audit["weights"]["sample_sha256"]
        assert audit["validation_sha256"] == audit["validation_weights"]["sample_sha256"]
        assert ("uniqueness" in audit["weights"]) == overlap_adjusted
        assert ("uniqueness" in audit["validation_weights"]) == overlap_adjusted
    architectures = ("linear-v1",) if model_family == "linear" else study.MODELS
    assert len(list(out.glob("seed-*/folds/*/*/checkpoint.pt"))) == (
        3 * len(architectures) if allow else 0
    )
    r = json.loads((out / "results.json").read_text())
    assert len(r["historical"]) == 12 * (3 + len(architectures)) and not r["live_promotion"]
    if not allow:
        assert all(
            Decimal(x["median_return"]) == 0
            for x in r["historical"]
            if x["candidate"] in (*architectures, "prior")
        )
    assert len(json.loads((out / "seed-17/audits.json").read_text())) == 1
    assert not (out / "failure.json").exists()
    artifact = next(out.glob("seed-17/continuous/*/results.json"))
    content = json.loads(artifact.read_text())
    content.pop()
    artifact.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="누락"):
        study.summarize(out, models=architectures)


def test_failure_does_not_report_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> list[list[Candle]]:
        raise ValueError("raw hash mismatch")

    with pytest.raises(ValueError, match="검증 기간"):
        study.run_study(tmp_path, tmp_path / "invalid", validation_quarters=3)
    with pytest.raises(ValueError, match="손익분기"):
        study.run_study(tmp_path, tmp_path / "invalid", threshold_mode="payoff")
    assert not (tmp_path / "invalid").exists()
    monkeypatch.setattr(study, "contiguous_blocks", fail)
    with pytest.raises(ValueError, match="hash"):
        study.run_study(tmp_path, tmp_path / "failure")
    assert (tmp_path / "failure/failure.json").exists()
    assert not (tmp_path / "failure/status.json").exists()


def test_payoff_uses_only_matching_purged_training_events() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=100)
    signals = (start, start + timedelta(hours=1))
    labels = (start + timedelta(hours=10), start + timedelta(hours=11))
    events: list[dict[str, object]] = [
        {
            "signal_time": t.isoformat(),
            "label_time": label.isoformat(),
            "label": outcome,
            "net_return": value,
            "status": "completed",
        }
        for t, label, outcome, value in zip(signals, labels, [1, 0], [".10", "-.025"], strict=True)
    ]
    events += [
        {**events[0], "label_time": end.isoformat(), "net_return": "1000"},
        {
            **events[0],
            "signal_time": (start - timedelta(hours=1)).isoformat(),
            "net_return": "1000",
        },
        {"status": "censored_at_gap_or_end"},
    ]
    threshold, audit = payoff_threshold(events, start, end)
    samples = Samples(torch.zeros(2, 32, 5), torch.tensor([1.0, 0.0]), signals, labels)
    assert threshold == Decimal(".2")
    assert audit["events"] == 2 and audit["sample_sha256"] == sample_identity([samples])
    with pytest.raises(ValueError, match="클래스"):
        payoff_threshold(events[:1], start, end)
