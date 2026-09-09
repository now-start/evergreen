import json
from decimal import Decimal

import pytest

from evergreen.research.experiments import breakout_errors
from evergreen.research.experiments.breakout_errors import analyze, event_metrics, trade_pnl


def test_event_metrics_separates_missed_winners_and_avoided_losses():
    rows = [
        {"score": ".8", "threshold": ".5", "prior": ".5", "net_return": ".2"},
        {"score": ".1", "threshold": ".5", "prior": ".5", "net_return": ".1"},
        {"score": ".7", "threshold": ".5", "prior": ".5", "net_return": "-.1"},
        {"score": ".2", "threshold": ".5", "prior": ".5", "net_return": "-.2"},
    ]
    result = event_metrics(rows, strict=True)
    assert [result[k] for k in ("tp", "fn", "fp", "tn")] == [1, 1, 1, 1]
    assert Decimal(result["accepted_mean_return"]) == Decimal(".05")
    assert Decimal(result["rejected_mean_return"]) == Decimal("-.05")
    assert Decimal(result["brier"]) == Decimal(".345")
    assert Decimal(result["prior_brier"]) == Decimal(".25")
    rows[0]["score"] = ".5"
    assert event_metrics(rows, strict=True)["tp"] == 0
    assert event_metrics(rows, strict=False)["tp"] == 1


def test_empty_events_do_not_imply_perfect_score():
    assert event_metrics([], strict=True)["brier"] is None


@pytest.mark.parametrize(
    "experiment,mode",
    [
        ("16", None),
        ("17", "payoff"),
        ("19", "weighted-score"),
        ("20", "fixed"),
        ("20", "payoff"),
        ("20", "weighted-score"),
        ("21", "weighted-score"),
        ("22", "weighted-score"),
    ],
)
def test_analysis_checks_prediction_coverage_and_legacy_fixed_protocol(
    tmp_path, monkeypatch, experiment, mode
):
    monkeypatch.setattr(breakout_errors, "SEEDS", (17,))
    model = "linear-v1" if experiment == "20" else "mlp-v1"
    monkeypatch.setattr(breakout_errors, "MODELS", (model,))
    source = tmp_path / "source"

    def save(name, value):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    start, end = "2022-01-01T00:00:00+00:00", "2022-01-01T01:00:00+00:00"
    save(
        "protocol.json",
        {
            "experiment": experiment,
            "entry_threshold": ".50",
            **({"threshold_mode": mode} if mode else {}),
        },
    )
    save("status.json", {"status": "completed_partial_coverage"})
    save(
        "events.json",
        [
            {"status": "completed", "signal_time": start, "net_return": ".1"},
            {"status": "completed", "signal_time": end, "net_return": ".2"},
        ],
    )
    save(
        "seed-17/audits.json",
        [
            {
                "start": start,
                "status": "trained",
                "training": {"positives": 2, "samples": 4},
                "entry_threshold": ".50",
                "weights": {"weighted_prior": ".8"},
            }
        ],
    )
    prediction = f"seed-17/folds/2022-01-01/scores.{model}.json"
    save(prediction, {start: ".8", end: ".9"})
    result = {
        "start": start,
        "end": end,
        "costs": {},
        "initial_capital": "100",
        "final_equity": "100",
        "btc": "0",
        "fills": [],
        "equity_curve": [{"time": end, "phase": "close"}],
    }
    for candidate in ("breakout-v1", model):
        save(f"seed-17/continuous/block-000/base.{candidate}.json", result)
    output = tmp_path / "output"
    analyze(source, output)
    metrics = json.loads((output / "results.json").read_text())[0]["metrics"]
    assert metrics["events"] == 1
    assert Decimal(metrics["prior_brier"]) == Decimal(".04" if mode == "weighted-score" else ".25")
    assert json.loads((output / "inputs.json").read_text())["sha256"][prediction]
    save(prediction, {start: ".8"})
    with pytest.raises(ValueError, match="시간 범위"):
        analyze(source, tmp_path / "missing")
    assert not (tmp_path / "missing/status.json").exists()


@pytest.mark.parametrize("score", ("NaN", "1.1", "-.1"))
def test_event_metrics_rejects_invalid_scores(score):
    with pytest.raises(ValueError):
        event_metrics(
            [{"score": score, "threshold": ".5", "prior": ".5", "net_return": ".1"}], strict=True
        )


def test_trade_pnl_reconciles_fees_and_settlement():
    result = {
        "initial_capital": "100",
        "final_equity": "108",
        "btc": "0",
        "fills": [
            {
                "side": "buy",
                "signal_time": "2022-01-01T00:00:00Z",
                "price": "10",
                "quantity": "9",
                "fee": "1",
            },
            {
                "side": "sell",
                "signal_time": "2022-01-02T00:00:00Z",
                "price": "11",
                "quantity": "9",
                "fee": "0",
                "reason": "settlement",
            },
        ],
    }
    rows = trade_pnl(result)
    assert rows["2022-01-01T00:00:00+00:00"] == Decimal(8)
    result["final_equity"] = "109"
    with pytest.raises(ValueError, match="손익"):
        trade_pnl(result)
    result["final_equity"] = "108"
    result["fills"][1]["quantity"] = "8"
    with pytest.raises(ValueError, match="수량"):
        trade_pnl(result)
