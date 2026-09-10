import hashlib
import json
from decimal import Decimal as D

import pytest
from test_breakout_entry_stop_trend_context import MODES
from test_breakout_liquidation_buffer import path

from evergreen.market import write_json as save_json
from evergreen.research import backtest
from evergreen.research.experiments import buffer_improvement as runner
from evergreen.research.experiments.breakout_meta import costs


def replay(bars, activation=None, delay=0):
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        D(1000000),
        costs(),
        "breakout-v1",
        breakout_liquidation_buffer=True,
        extra_delay_bars=delay,
        exit_policy=backtest.ExitPolicy(
            drawdown_limit=D(".2"),
            stop_multiple=D(6),
            channel_hours=96,
            profit_activation_multiple=activation,
        ),
        **MODES,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_explicit_original_activation_is_exactly_unchanged(delay):
    bars = path()
    assert replay(bars, delay=delay) == replay(bars, D(6), delay)


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("peak,early", [("109.999999", False), ("110", True)])
def test_early_activation_exact_threshold_and_pending_rebound(monkeypatch, delay, peak, early):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    # Prior means are flat, so a trailing breach is not suppressed by an uptrend.
    for i in range(200, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(104), "high": D(105), "low": D(103), "close": D(104)}
        )
    bars[202] = bars[202].model_copy(update={"close": D(peak), "high": D(110)})
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": D(97), "low": D(96)})
    # The comparison means include the peak: force a genuinely declining prior trend.
    for i in range(155, 179):
        bars[i] = bars[i].model_copy(update={"close": D(101), "high": D(102)})
    r = replay(bars, D(3), delay)
    baseline = replay(bars, None, delay)
    assert (r.fills[1].signal_time == bars[204].close_time) is early
    if early:
        assert r.fills[1].time == bars[205 + delay].open_time
        assert r.fills[1].time < baseline.fills[1].time
        assert any(b.open_time > r.fills[1].time for b in bars)
        changed = [
            b.model_copy(update={"close": D(150), "high": D(151)})
            if b.open_time > r.fills[1].time
            else b
            for b in bars
        ]
        assert replay(changed, D(3), delay).fills[:2] == r.fills[:2]
    assert r.fills[0] == baseline.fills[0]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "2", "7"])
def test_activation_range_is_bounded(value):
    with pytest.raises(ValueError):
        backtest.ExitPolicy(profit_activation_multiple=D(value))


def test_activation_requires_profit_trailing():
    bars = path()
    with pytest.raises(ValueError, match="추적"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            D(1000000),
            costs(),
            "breakout-v1",
            exit_policy=backtest.ExitPolicy(profit_activation_multiple=D(3)),
        )


def test_initial_stop_still_exits_without_profit_activation(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": D(91), "low": D(90)})
    r = replay(bars, D(3))
    assert r.fills[1].signal_time == bars[204].close_time
    assert r.fills[1].reason == "signal"
    assert r.fills[:2] == replay(bars, None).fills[:2]


def test_rising_trend_defers_early_trailing(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    for i in range(155, 179):
        bars[i] = bars[i].model_copy(update={"close": D(99)})
    bars[202] = bars[202].model_copy(update={"close": D(110), "high": D(111)})
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": D(97), "low": D(96)})
    r = replay(bars, D(3))
    assert r.fills[1].time > bars[205].open_time


def test_same_signal_diagnostic_reconciles_and_marks_settlement():
    bars = path()
    r = replay(bars)
    effect = runner.entry_effects(r, r)
    assert effect["shared"] == len(r.fills) // 2
    assert not effect["added"] and not effect["removed"]
    assert all(D(x["return_change"]) == 0 for x in effect["matched"])
    assert all("boundary_settlement" in x["candidate"] for x in effect["matched"])
    with pytest.raises(ValueError, match="불일치"):
        runner.entry_effects(r, replay(bars, delay=1))
    with pytest.raises(ValueError):
        runner.entry_effects(r.model_copy(update={"final_equity": r.final_equity + 1}), r)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, data)


@pytest.mark.parametrize("damage", [None, "result", "dataset"])
def test_study_checks_pinned_sources_controls_and_keeps_domains(tmp_path, damage):
    bars = path()
    root = tmp_path / "inputs"
    reference = root / "relaxed-exits-71-verified"
    report = {}
    inputs = {}
    for domain, dataset_name in (
        ("recent", "breakout-entry-cadence-67"),
        ("historical", "historical-window-70"),
    ):
        dataset = root / dataset_name / "datasets/block-000"
        dataset.mkdir(parents=True)
        raw = "".join(b.model_dump_json() + "\n" for b in bars)
        (dataset / "candles.jsonl").write_text(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        write_json(
            dataset / "quality.json",
            {
                "status": "passed",
                "sha256": digest,
                "start": bars[0].open_time.isoformat(),
                "end": bars[-1].close_time.isoformat(),
                "as_of": bars[-1].fetched_at.isoformat(),
            },
        )
        for file in (dataset / "quality.json", dataset / "candles.jsonl"):
            inputs[str(file.resolve())] = hashlib.sha256(file.read_bytes()).hexdigest()
        report[domain] = {
            "intervals": [
                {"start": bars[200].open_time.isoformat(), "end": bars[-1].close_time.isoformat()}
            ],
            "evaluated_hours": 60,
        }
        for scenario, fee, slip, delay in runner.SCENARIOS:
            for name, model, policy in (
                ("wide20", "liquidation-buffer", runner.WIDE),
                ("original", "liquidation-buffer", backtest.ExitPolicy()),
                ("wide20", "breakout-v1", runner.WIDE),
            ):
                r = runner.simulate(
                    bars, bars[200].open_time, model, policy, costs(fee, slip), delay, {}
                )
                write_json(
                    reference / domain / "block-000" / f"{name}.{scenario}.{model}.json",
                    r.model_dump(mode="json"),
                )
    write_json(reference / "protocol.json", {"experiment": "71"})
    write_json(reference / "status.json", {"status": "completed_partial_coverage"})
    write_json(reference / "results.json", report)
    write_json(reference / "inputs.json", inputs)
    write_json(
        reference / "artifacts.json",
        {
            str(p.relative_to(reference)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in reference.rglob("*.json")
        },
    )
    if damage == "result":
        (reference / "recent/block-000/wide20.base.liquidation-buffer.json").write_text("{}")
    elif damage == "dataset":
        (root / "historical-window-70/datasets/block-000/candles.jsonl").write_text("")
    out = tmp_path / "out"
    if damage:
        with pytest.raises(ValueError):
            runner.run_study(root, out)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(root, out)
        status = json.loads((out / "status.json").read_text())
        assert status["pnl_checks"] == 56 and status["original_json_matches"] == 24
        results = json.loads((out / "results.json").read_text())
        assert set(results) == {"recent", "historical"}
        assert len(results["recent"]["ranking"]) == 7
        assert "paired_median_vs_wide20" in results["recent"]["summaries"]["base"]["early3"]
        assert not status["live_enabled"]
        with pytest.raises(FileExistsError):
            runner.run_study(root, out)
