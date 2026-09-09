from datetime import UTC, datetime, timedelta

import pytest
import torch

from evergreen.research.learning.breakout_meta import payoff_weights
from evergreen.research.learning.models import Samples, load_model, train_model, validation_loss


def samples(offset=0):
    start = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=offset)
    return Samples(
        torch.arange(8 * 32 * 5, dtype=torch.float32).reshape(8, 32, 5) / 100,
        torch.tensor([0.0, 1.0] * 4),
        tuple(start + timedelta(hours=i) for i in range(8)),
        tuple(start + timedelta(hours=i + 1) for i in range(8)),
    )


def test_payoff_alignment_and_weighted_prior():
    s = samples()
    records = [
        {
            "status": "completed",
            "signal_time": t.isoformat(),
            "label_time": e.isoformat(),
            "label": int(y),
            "net_return": ".1" if y else "-.025",
        }
        for t, e, y in zip(s.signal_times, s.label_times, s.labels.tolist(), strict=True)
    ]
    weights, audit = payoff_weights(records, [s])
    assert torch.allclose(weights, torch.tensor([0.025, 0.1] * 4))
    assert float(audit["weighted_prior"]) == 0.8
    assert float(audit["top1_share"]) == 0.2
    assert audit["events"] == 8
    with pytest.raises(ValueError, match="일치"):
        payoff_weights([{**records[0], "label_time": records[1]["label_time"]}, *records[1:]], [s])
    with pytest.raises(ValueError, match="누락"):
        payoff_weights(records[1:], [s])


def test_weighted_early_stop_restore_and_uniform_parity(tmp_path):
    train, valid = samples(), samples(10)
    options = dict(epochs=6, validation=[valid], patience=2, seed=17)
    plain = train_model([train], "mlp-v1", tmp_path / "plain", **options)
    uniform = train_model(
        [train],
        "mlp-v1",
        tmp_path / "uniform",
        **options,
        sample_weights=torch.ones(8),
        validation_weights=torch.ones(8),
    )
    assert torch.equal(plain.probabilities(valid.features), uniform.probabilities(valid.features))
    weighted = train_model(
        [train],
        "mlp-v1",
        tmp_path / "weighted",
        **options,
        sample_weights=torch.tensor([0.025, 0.1] * 4),
        validation_weights=torch.tensor([0.025, 0.1] * 4),
    )
    restored = load_model(tmp_path / "weighted")
    assert restored.metadata.loss_weighting == "sample"
    assert torch.equal(
        weighted.probabilities(valid.features), restored.probabilities(valid.features)
    )
    normalized = ((valid.features - restored.mean) / restored.scale).clamp(-10, 10)
    loss = validation_loss(restored.model, normalized, valid.labels, torch.tensor([0.4, 1.6] * 4))
    assert loss == pytest.approx(min(weighted.metadata.validation_losses))
    assert not torch.equal(
        plain.probabilities(valid.features), weighted.probabilities(valid.features)
    )


@pytest.mark.parametrize(
    "weights", [torch.zeros(8), -torch.ones(8), torch.ones(7), torch.full((8,), float("nan"))]
)
def test_invalid_weights_fail_before_artifact(tmp_path, weights):
    with pytest.raises(ValueError, match="가중치"):
        train_model([samples()], "mlp-v1", tmp_path / "bad", sample_weights=weights)
    assert not (tmp_path / "bad").exists()


def test_float32_weight_overflow_fails(tmp_path):
    with pytest.raises(ValueError, match="가중치"):
        train_model([samples()], "mlp-v1", tmp_path / "bad", sample_weights=torch.full((8,), 1e38))


def test_zero_weight_is_valid_and_no_weighted_validation_without_training(tmp_path):
    fitted = train_model(
        [samples()],
        "mlp-v1",
        tmp_path / "zero",
        epochs=1,
        sample_weights=torch.tensor([0.0, 1.0] * 4),
    )
    assert fitted.metadata.loss_weighting == "sample"
    with pytest.raises(ValueError, match="가중치"):
        train_model([samples()], "mlp-v1", tmp_path / "bad", validation_weights=torch.ones(8))


def test_validation_weight_configuration_cannot_be_silent(tmp_path):
    with pytest.raises(ValueError, match="가중치"):
        train_model(
            [samples()],
            "mlp-v1",
            tmp_path / "bad",
            validation=[samples(10)],
            sample_weights=torch.ones(8),
        )
