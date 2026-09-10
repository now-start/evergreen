from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from decimal import Decimal as D
from pathlib import Path
from typing import Literal

import pytest
from test_breakout_entry_stop_trend_context import MODES
from test_breakout_exit_checkpoint import checkpoint_path
from test_breakout_liquidation_buffer import path

from evergreen.market import Candle
from evergreen.research import backtest
from evergreen.research.backtest import Result
from evergreen.research.experiments.breakout_meta import costs


def simulate(
    bars: list[Candle], *, entry: datetime | None = None, delay: int = 0, lookback: int = 24
) -> Result:
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        D(1000000),
        costs(),
        "regime-mlp-v1",
        regime_policy="breakout-filter",
        regimes={b.close_time: 2 for b in bars[199:]},
        breakout_liquidation_buffer=True,
        breakout_entry_stop_trend_lookback=lookback,
        long_entry_at=entry,
        extra_delay_bars=delay,
        **MODES,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_target_first_entry_matches_long_control_and_keeps_entry_prefix(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = checkpoint_path()
    original = simulate(bars, delay=delay)
    changed = simulate(bars, entry=bars[199].close_time, delay=delay)
    assert changed == simulate(bars, lookback=168, delay=delay)
    assert changed.fills[0] == original.fills[0]
    assert [p for p in changed.equity_curve if p.time <= changed.fills[0].time] == [
        p for p in original.equity_curve if p.time <= original.fills[0].time
    ]


def test_unmatched_or_mixed_entry_fork_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    with pytest.raises(ValueError, match="진입 분기"):
        simulate(bars, entry=bars[198].close_time)
    with pytest.raises(ValueError, match="진입 분기"):
        simulate(bars, entry=bars[199].close_time, lookback=168)


def test_only_target_position_uses_long_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(110), "high": D(111)})
    bars[249] = bars[249].model_copy(update={"close": D(95), "low": D(94)})
    bars[250] = bars[250].model_copy(update={"close": D(115), "high": D(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(115), "close": D(115), "high": D(116), "low": D(114)}
        )
    seen = []
    mark = backtest._Portfolio.mark

    def record(
        self: backtest._Portfolio,
        price: D,
        time: datetime,
        phase: Literal["open", "close", "settlement"],
    ) -> None:
        if (
            phase == "open"
            and self.fills
            and self.fills[-1].time == time
            and self.fills[-1].side == "buy"
        ):
            seen.append(sys._getframe(1).f_locals["position_trend_lookback"])
        return mark(self, price, time, phase)

    monkeypatch.setattr(backtest._Portfolio, "mark", record)
    changed = simulate(bars, entry=bars[199].close_time)
    assert seen == [168, 24]
    assert changed == simulate(bars)
    assert len(changed.fills) == 4
    assert changed.fills[2].price * changed.fills[2].quantity + changed.fills[2].fee > D(1000000)


def test_pair_labels_censor_and_reject_changed_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.research.experiments.breakout_entry_counterfactual import pair_record

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = checkpoint_path()
    original = simulate(bars)
    changed = simulate(bars, entry=bars[199].close_time)
    record = pair_record(original, changed, original.fills[0])
    assert record["status"] == "censored" and record["label"] is None
    damaged = changed.model_copy(deep=True)
    damaged.fills[0] = damaged.fills[0].model_copy(update={"fee": D(123)})
    with pytest.raises(ValueError, match=r"진입|경로"):
        pair_record(original, damaged, original.fills[0])


def test_completed_tie_and_later_exit_label_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.research.experiments.breakout_entry_counterfactual import pair_record

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    original = simulate(bars)
    record = pair_record(original, original, original.fills[0])
    assert record["label"] == "tie" and D(record["pnl_delta"]) == 0
    assert record["label_time"] == original.fills[1].time.isoformat()
    assert record["prefix_equal"]


@pytest.mark.parametrize(
    "delta,label",
    [
        (D("0.000001"), "tie"),
        (D("-0.000001"), "tie"),
        (D("0.0000011"), "long"),
        (D("-0.0000011"), "short"),
    ],
)
def test_label_tolerance_is_symmetric(
    monkeypatch: pytest.MonkeyPatch, delta: Decimal, label: str
) -> None:
    from evergreen.research.experiments.breakout_entry_counterfactual import pair_record

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    original = simulate(path())
    changed = original.model_copy(deep=True)
    sell = changed.fills[1]
    changed.fills[1] = sell.model_copy(
        update={"fee": sell.fee - delta, "cash_after": sell.cash_after + delta}
    )
    record = pair_record(original, changed, original.fills[0])
    assert record["label"] == label and D(record["pnl_delta"]) == delta


def test_features_ignore_future_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.research.experiments.breakout_entry_counterfactual import entry_features

    bars = checkpoint_path()
    signal = bars[199].close_time
    before = entry_features(bars, signal)
    bars[201] = bars[201].model_copy(update={"close": D(100000), "high": D(100001)})
    assert entry_features(bars, signal) == before
    with pytest.raises(ValueError, match="신호"):
        entry_features(bars, signal - timedelta(minutes=1))


def test_completed_label_waits_for_both_exits_and_checks_curve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evergreen.research.experiments.breakout_entry_counterfactual import pair_record

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = checkpoint_path()
    bars[230] = bars[230].model_copy(update={"close": D(97), "low": D(96)})
    original = simulate(bars)
    changed = simulate(bars, entry=bars[199].close_time)
    record = pair_record(original, changed, original.fills[0])
    assert record["status"] == "completed"
    assert record["label_time"] == max(original.fills[1].time, changed.fills[1].time).isoformat()
    assert D(record["pnl_delta"]) == D(record["long"]["net_pnl"]) - D(record["short"]["net_pnl"])
    damaged = changed.model_copy(deep=True)
    damaged.equity_curve[0] = damaged.equity_curve[0].model_copy(update={"equity": D(123)})
    with pytest.raises(ValueError, match="계좌 경로"):
        pair_record(original, damaged, original.fills[0])


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_full_control_and_records_immutable_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from evergreen.market import write_json
    from evergreen.research.experiments import breakout_entry_counterfactual as study
    from evergreen.research.experiments.breakout_meta import evaluate
    from evergreen.research.experiments.regime import Evaluation

    bars = path()
    start = bars[200].open_time
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    source = tmp_path / "source"
    dataset = source / "datasets/block-000"
    dataset.mkdir(parents=True)
    (dataset / "candles.jsonl").write_text("fixture")
    write_json(dataset / "quality.json", {"fixture": True})
    write_json(source / "datasets/coverage.json", {"fixture": True})
    write_json(source / "protocol.json", {"experiment": "60"})
    write_json(source / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        source / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    monkeypatch.setattr(study, "load_dataset", lambda p: (bars, "fixture"))
    models = study.MODELS[3:]
    evaluate(
        {n: [Evaluation(start, bars, {b.close_time: 2 for b in bars[199:]})] for n in study.MODELS},
        source / "seed-17",
        models=models,
        entry_stop_models=models,
        entry_stop_confirmations=dict.fromkeys(models, 2),
        entry_stop_channel_reset_models=models,
        entry_stop_profit_trail_models=models,
        entry_stop_adaptive_trail_models=models,
        entry_stop_trend_confirmation_models=models,
        entry_stop_budget_models=models,
        entry_stop_trend_lookbacks={
            "entry-stop-trend-context": 168,
            "liquidation-buffer-long": 168,
        },
        liquidation_buffer_models=tuple(models[2:]),
        entry_context_models=("entry-context",),
        entry_path_context_models=("entry-path-context",),
    )
    if corrupt:
        p = source / "seed-17/continuous/block-000/base.liquidation-buffer.json"
        payload = json.loads(p.read_text())
        payload["total_fees"] = "123"
        p.write_text(json.dumps(payload))
    output = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="전체 결과"):
            study.analyze(source, output)
        assert (output / "failure.json").exists() and not (output / "status.json").exists()
    else:
        study.analyze(source, output)
        status = json.loads((output / "status.json").read_text())
        assert (
            status["source_pnl_checks"] == 36
            and status["baseline_comparisons"] == 4
            and status["entry_comparisons"] == 4
        )
        records = json.loads((output / "records.json").read_text())
        assert all(r["prefix_equal"] and (output / r["counterfactual"]).exists() for r in records)
        assert not json.loads((output / "results.json").read_text())["profitability_gate_passed"]
        with pytest.raises(FileExistsError):
            study.analyze(source, output)
