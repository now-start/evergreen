from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_trend_confirmation import declining_path
from test_breakout_entry_stop_trend_context import MODES, long_decline

from evergreen.market import write_json
from evergreen.research import backtest
from evergreen.research.experiments import breakout_exit_extension as runner
from evergreen.research.experiments.breakout_meta import costs


def simulate(bars, *, events=None, extend=None, delay=0, capital=Decimal(1000000)):
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        capital,
        costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        exit_decisions=events,
        extend_exit_at=extend,
        **MODES,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_fork_has_identical_prefix_and_keeps_long_counter(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    decisions = []
    original = simulate(bars, events=decisions, delay=delay)
    assert original == simulate(bars, delay=delay)
    decision = decisions[0]
    assert decision.signal_time == bars[204].close_time
    assert decision.short_breaches == 2
    assert decision.long_breaches == 0
    replay = []
    extended = simulate(bars, events=replay, extend=decision.signal_time, delay=delay)
    assert replay[0] == decision
    assert original.fills[0] == extended.fills[0] == decision.entry_fill
    assert extended.fills[1].time > original.fills[1].time
    assert decision.account_peak > original.initial_capital
    assert decision.cash == original.fills[0].cash_after
    assert decision.btc == original.fills[0].btc_after
    assert [
        p
        for p in original.equity_curve
        if p.time < decision.signal_time or (p.time == decision.signal_time and p.phase == "close")
    ] == [
        p
        for p in extended.equity_curve
        if p.time < decision.signal_time or (p.time == decision.signal_time and p.phase == "close")
    ]


def test_no_disagreement_keeps_same_fills(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars, events = long_decline(), []
    original = simulate(bars, events=events)
    assert events[0].long_breaches >= 2
    assert simulate(bars, extend=events[0].signal_time) == original


def test_future_candles_cannot_change_decision_state(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars, before, after = declining_path(), [], []
    simulate(bars, events=before)
    for i in range(205, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(120),
                "high": Decimal(122),
                "low": Decimal(119),
                "close": Decimal(121),
            }
        )
    simulate(bars, events=after)
    assert before[0] == after[0]


@pytest.mark.parametrize("kind", ["channel", "risk"])
def test_channel_and_risk_are_not_extension_events(monkeypatch, kind):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars, events = setup(), []
    if kind == "channel":
        bars[160] = bars[160].model_copy(update={"low": Decimal(99)})
        bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    bars[203] = bars[203].model_copy(
        update={"close": Decimal(98 if kind == "channel" else 70), "low": Decimal(69)}
    )
    result = simulate(bars, events=events)
    assert result.fills[1].reason == ("signal" if kind == "channel" else "risk")
    assert not events


def test_fork_after_history_or_without_policy50_is_rejected():
    bars = setup()
    with pytest.raises(ValueError, match="청산 분기"):
        simulate(bars, extend=bars[-1].close_time + timedelta(hours=1))
    with pytest.raises(ValueError, match="청산 분기"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            exit_decisions=[],
        )


def test_first_decision_only_even_if_sell_rejected(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars, events = declining_path(), []
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    result = simulate(bars, events=events, capital=Decimal(5100))
    assert result.rejections[0].side == "sell"
    assert len({event.entry_fill.time for event in events}) == len(events) == 1


@pytest.mark.parametrize("delay", [0, 1])
def test_collector_reconciles_current_position_cash_and_label_availability(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    # Finish the long branch naturally, well before boundary settlement.
    bars[220] = bars[220].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    original, events = runner.collect_events(bars, bars[200].open_time, delay=delay)
    event = events[0]
    assert event["status"] == "completed"
    a, b = (backtest.Fill.model_validate(event[k]) for k in ("exit_a", "exit_b"))
    assert a == original.fills[1]
    assert b.reason == "risk"
    assert event["label_time"] == (max(a.time, b.time) + timedelta(hours=1)).isoformat()
    difference = b.cash_after - a.cash_after
    assert Decimal(event["cash_difference"]) == difference
    assert Decimal(event["incremental_return"]) == difference / Decimal(event["state"]["equity"])
    assert event["label"] == int(difference > 0)
    assert event["prefix_equal"]
    assert "label" not in event["features"] and "exit_time" not in event["features"]


def test_boundary_and_identical_exits_are_not_training_examples(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()[:215]
    _, censored = runner.collect_events(bars, bars[200].open_time)
    assert censored[0]["status"] == "censored_at_gap_or_end"
    assert censored[0]["label"] is None and censored[0]["label_time"] is None
    bars = long_decline()
    _, identical = runner.collect_events(bars, bars[200].open_time)
    assert identical[0]["status"] == "no_disagreement"
    assert runner.audit_folds(censored + identical)["completed_counts"]["samples"] == 0


def test_cash_reconciliation_rejects_mismatched_branch(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars, events = declining_path(), []
    result = simulate(bars, events=events)
    tampered = result.model_copy(deep=True)
    tampered.fills[1].cash_after += 1
    with pytest.raises(ValueError, match="정합성"):
        runner.position_exit(tampered, events[0])
    tampered = result.model_copy(deep=True)
    tampered.fills[0].cash_after += 1
    with pytest.raises(ValueError, match="진입 체결"):
        runner.position_exit(tampered, events[0])


def test_features_are_point_in_time_and_rejection_is_excluded(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars, decisions = declining_path(), []
    original = simulate(bars, events=decisions)
    decision = decisions[0]
    features = runner.decision_features(bars, decision)
    assert features == runner.decision_features(bars[:205], decision)
    assert "cash_difference" not in features
    with pytest.raises(ValueError, match="확정 봉"):
        runner.decision_features(bars[:204], decision)
    changed = original.model_copy(deep=True)
    changed.rejections.append(
        backtest.Rejection(time=changed.fills[1].time, side="sell", trigger="signal")
    )
    assert runner.label_record(decision, changed, original)["status"] == "exit_rejected"
    changed = original.model_copy(deep=True)
    changed.fills[1].reason = "risk"
    assert (
        runner.label_record(decision, changed, original)["status"]
        == "original_exit_unfilled_or_replaced"
    )


def test_time_splits_purge_boundary_labels_and_do_not_lower_eligibility():
    def record(signal, end, label=1):
        return {"status": "completed", "signal_time": signal, "label_time": end, "label": label}

    records = [
        record("2020-01-02T00:00:00+00:00", "2020-01-03T00:00:00+00:00"),
        record("2021-06-30T23:00:00+00:00", "2021-07-01T00:00:00+00:00", 0),
        record("2021-07-01T01:00:00+00:00", "2021-07-01T03:00:00+00:00", 0),
        record("2021-12-31T23:00:00+00:00", "2022-01-01T01:00:00+00:00"),
        record("2022-01-02T00:00:00+00:00", "2022-01-02T03:00:00+00:00"),
    ]
    audit = runner.audit_folds(records)
    first = audit["folds"][0]
    assert first["test_start"] == datetime(2022, 1, 1, tzinfo=UTC).isoformat()
    assert first["training"]["samples"] == first["validation"]["samples"] == 1
    assert first["purged"] == {"training": 1, "validation": 1, "test_audit_only": 0}
    assert first["test_audit_only"]["samples"] == 1
    assert not first["eligible"] and audit["eligible_folds"] == 0
    with pytest.raises(ValueError, match="중복"):
        runner.audit_folds(records + records[:1])


def test_overlapping_events_do_not_inflate_independent_groups():
    records = []
    start = datetime(2020, 1, 2, tzinfo=UTC)
    for i in range(50):
        records.append(
            {
                "status": "completed",
                "signal_time": (start + timedelta(hours=i)).isoformat(),
                "label_time": (start + timedelta(hours=60)).isoformat(),
                "label": i % 2,
            }
        )
    audit = runner.audit_folds(records)
    assert audit["completed_counts"] == {
        "samples": 50,
        "positives": 25,
        "negatives": 25,
        "non_overlapping_groups": 1,
    }
    assert audit["eligible_folds"] == 0


def test_incomplete_reference_fails_closed_without_completed_status(tmp_path):
    reference = tmp_path / "reference"
    reference.mkdir()
    write_json(reference / "protocol.json", {"experiment": "51"})
    write_json(reference / "status.json", {"status": "failed"})
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="완료된 실험51"):
        runner.run_study(tmp_path / "raw", reference, output)
    assert (output / "failure.json").exists()
    assert not (output / "status.json").exists()
    with pytest.raises(FileExistsError):
        runner.run_study(tmp_path / "raw", reference, output)
