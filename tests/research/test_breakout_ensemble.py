import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from test_learning import candles

from evergreen.market import write_json
from evergreen.research.experiments import breakout_ensemble as ensemble
from evergreen.research.learning.models import ModelMetadata


def test_equal_scores_not_majority_vote_or_best_seed():
    t = datetime(2022, 1, 1, tzinfo=UTC)
    members = [{t: Decimal(p)} for p in (".9", ".4", ".4")]
    assert ensemble.mean_scores(members, {t})[t] == sum((m[t] for m in members), Decimal(0)) / 3
    assert ensemble.mean_scores(members[::-1], {t}) == ensemble.mean_scores(members, {t})
    assert ensemble.mean_scores([{t: Decimal(".5")}] * 3, {t})[t] == Decimal(".5")
    for bad in (
        members[:2],
        [*members[:2], {}],
        [*members[:2], {t: Decimal("NaN")}],
        [*members[:2], {t: Decimal("1.1")}],
    ):
        with pytest.raises(ValueError):
            ensemble.mean_scores(bad, {t})


@pytest.mark.parametrize("missing", [False, True])
def test_frozen_pipeline_and_missing_member_fail_closed(tmp_path, monkeypatch, missing):
    dates = [datetime(2020 + i // 4, i % 4 * 3 + 1, 1, tzinfo=UTC) for i in range(10)]
    periods = list(pairwise(dates))
    start = dates[8]
    bars = [
        b.model_copy(
            update={
                "open_time": start - timedelta(hours=200) + timedelta(hours=i),
                "fetched_at": datetime(2025, 1, 1, tzinfo=UTC),
            }
        )
        for i, b in enumerate(candles(320))
    ]

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(ensemble, "contiguous_blocks", blocks)
    monkeypatch.setattr(ensemble, "quarters", lambda: periods)
    for name, number, adjusted in [("control", "21", False), ("candidate", "22", True)]:
        root = tmp_path / name
        root.mkdir()
        (root / "datasets").mkdir()
        write_json(root / "datasets/coverage.json", {"fixture": True})
        write_json(root / "events.json", [])
        write_json(root / "status.json", {"status": "completed_partial_coverage"})
        write_json(
            root / "protocol.json",
            {
                "experiment": number,
                "feature_set": "short-200",
                "warmup": 200,
                "seeds": list(ensemble.MEMBERS),
                "threshold_mode": "weighted-score",
                "loss_weighting": "absolute-return",
                "overlap_adjusted": adjusted,
                "training_quarters": 6,
                "validation_quarters": 2,
                "entry_threshold": ".5",
                "base_costs": ensemble.costs().model_dump(mode="json"),
            },
        )
        audit = {
            "start": start.isoformat(),
            "status": "trained",
            "entry_threshold": ".5",
            "weights": {"weighted_prior": ".5"},
            "training_sha256": "training",
            "validation_sha256": "validation",
        }
        for seed in ensemble.MEMBERS:
            group = root / f"seed-{seed}"
            folder = group / "folds" / str(start.date())
            model = folder / "mlp-v1"
            model.mkdir(parents=True)
            write_json(group / "audits.json", [audit])
            checkpoint = b"fixture hash only: no model inference in frozen-score replay"
            (model / "checkpoint.pt").write_bytes(checkpoint)
            metadata = ModelMetadata(
                architecture="mlp-v1",
                feature_set="short-200",
                mean=[0] * 5,
                scale=[1] * 5,
                checkpoint_sha256=hashlib.sha256(checkpoint).hexdigest(),
                epochs=1,
                seed=seed,
                training_samples=2,
                positive_rate=0.5,
                losses=[0.7],
                torch_version="fixture",
                device="cpu",
                loss_weighting="sample",
            )
            write_json(model / "model.json", metadata.model_dump(mode="json"))
            write_json(
                model / "task.json",
                {
                    **audit,
                    "seed": seed,
                    "validation_label_max": (start - timedelta(hours=1)).isoformat(),
                },
            )
            if not (missing and name == "candidate" and seed == 43):
                write_json(
                    folder / "scores.mlp-v1.json",
                    {b.close_time.isoformat(): ".6" for b in bars[199:]},
                )
    output = tmp_path / "out"
    if missing:
        with pytest.raises(ValueError, match="예측 시간"):
            ensemble.run_ensemble(tmp_path, tmp_path / "control", tmp_path / "candidate", output)
        assert (output / "failure.json").exists() and not (output / "status.json").exists()
    else:
        ensemble.run_ensemble(tmp_path, tmp_path / "control", tmp_path / "candidate", output)
        import json

        status = json.loads((output / "status.json").read_text())
        assert not status["stability_proven"] and not status["additional_groups_required"]
        assert (output / "candidate/errors/results.json").exists()
        assert len(list((output / "candidate/seed-17/continuous/block-000").glob("*.json"))) == 17
        standalone = tmp_path / "standalone-errors"
        ensemble.analyze(output / "candidate", standalone)
        assert json.loads((standalone / "results.json").read_text()) == json.loads(
            (output / "candidate/errors/results.json").read_text()
        )
