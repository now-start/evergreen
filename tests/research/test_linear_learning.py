from __future__ import annotations

from pathlib import Path

import torch
from test_payoff_learning import samples

from evergreen.research.learning.models import ChartModel, load_model, train_model
from evergreen.strategies import STRATEGY_PARAMETERS


def test_linear_shape_capacity_and_not_an_operating_strategy() -> None:
    model = ChartModel("linear-v1")
    assert sum(p.numel() for p in model.parameters()) == 161
    assert model(torch.zeros(3, 32, 5)).shape == (3,)
    assert "linear-v1" not in STRATEGY_PARAMETERS


def test_linear_weighting_seed_restore(tmp_path: Path) -> None:
    train, valid = samples(), samples(10)
    fitted = []
    for index, (seed, weighted) in enumerate(((17, False), (29, False), (17, False), (17, True))):
        model = train_model(
            [train],
            "linear-v1",
            tmp_path / str(index),
            epochs=5,
            seed=seed,
            validation=[valid],
            patience=2,
            sample_weights=torch.tensor([0.025, 0.1] * 4) if weighted else None,
            validation_weights=torch.tensor([0.025, 0.1] * 4) if weighted else None,
        )
        restored = load_model(tmp_path / str(index))
        assert torch.equal(
            model.probabilities(valid.features), restored.probabilities(valid.features)
        )
        assert restored.metadata.checkpoint_epoch == restored.metadata.best_epoch
        fitted.append(model)
    assert torch.equal(
        fitted[0].probabilities(valid.features), fitted[2].probabilities(valid.features)
    )
    assert not torch.equal(
        fitted[0].probabilities(valid.features), fitted[1].probabilities(valid.features)
    )
    assert not torch.equal(
        fitted[0].probabilities(valid.features), fitted[3].probabilities(valid.features)
    )
