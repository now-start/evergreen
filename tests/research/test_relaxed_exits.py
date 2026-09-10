import hashlib
import json
from decimal import Decimal as D

import pytest
from test_breakout_entry_stop import setup
from test_breakout_liquidation_buffer import path, simulate

from evergreen.market import write_json as save_json
from evergreen.research import backtest
from evergreen.research.experiments import relaxed_exits as runner
from evergreen.research.experiments.breakout_meta import costs


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, data)


@pytest.mark.parametrize("hours", [72, 96])
@pytest.mark.parametrize("delay", [0, 1])
def test_wider_channel_excludes_current_low_and_waits(hours, delay):
    bars = setup()
    bars[249] = bars[249].model_copy(update={"close": D("100.5"), "low": D(1)})
    args = (bars, bars[200].open_time, D(1000000), costs(), "breakout-v1")
    old = backtest.run_backtest(*args, extra_delay_bars=delay)
    wide = backtest.run_backtest(
        *args, extra_delay_bars=delay, exit_policy=backtest.ExitPolicy(channel_hours=hours)
    )
    assert old.fills[1].time == bars[250 + delay].open_time
    assert wide.fills[1].reason == "settlement"
    assert old.fills[0] == wide.fills[0]


def test_relaxed_buffer_moves_asset_floor_not_range_multiplier(monkeypatch):
    from test_breakout_entry_stop_trend_context import MODES

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    old = simulate(bars)
    wide = backtest.run_backtest(
        bars,
        bars[200].open_time,
        D(1000000),
        costs(),
        "breakout-v1",
        breakout_liquidation_buffer=True,
        exit_policy=backtest.ExitPolicy(drawdown_limit=D(".15")),
        **MODES,
    )
    assert old.fills[1].signal_time == bars[203].close_time
    assert wide.fills[1].time > old.fills[1].time


def test_wider_stop_changes_entry_budget_not_position_fraction():
    from test_breakout_entry_stop_trend_context import MODES

    bars = setup()
    r = backtest.run_backtest(
        bars,
        bars[200].open_time,
        D(1000000),
        costs(),
        "breakout-v1",
        exit_policy=backtest.ExitPolicy(stop_multiple=D(6)),
        **MODES,
    )
    assert r.rejections[0].time == bars[200].open_time
    assert r.rejections[0].reason == "risk_budget"
    assert not any(f.time == bars[200].open_time for f in r.fills)


@pytest.mark.parametrize("damage", [None, "hash", "mean", "future", "missing"])
def test_frozen_predictions_are_verified(tmp_path, damage):
    inputs = runner.Inputs()
    root = tmp_path
    original = root / "breakout-ensemble-23"
    write_json(original / "protocol.json", {"members": [17, 29, 43], "experiment": "23"})
    write_json(original / "status.json", {"status": "completed_partial_coverage"})
    declared = {}
    timestamp = "2022-01-02T00:00:00+00:00"
    for seed in (17, 29, 43):
        directory = root / f"breakout-overlap-22-candidate/seed-{seed}/folds/2022-01-01"
        task = directory / "mlp-v1/task.json"
        forecast = directory / "piece.mlp-v1.json"
        write_json(
            task,
            {
                "validation_label_max": timestamp
                if damage == "future"
                else "2021-12-31T00:00:00+00:00"
            },
        )
        write_json(forecast, {timestamp: ".6"})
        for p in (task, forecast):
            declared[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
        if damage == "hash":
            declared[str(forecast.resolve())] = "bad"
        if damage == "missing":
            declared.pop(str(forecast.resolve()))
    write_json(original / "inputs.json", declared)
    write_json(
        original / "candidate/seed-17/folds/2022-01-01/piece.ensemble-mlp.json",
        {timestamp: ".8" if damage == "mean" else ".6"},
    )
    if damage:
        with pytest.raises(ValueError):
            runner.frozen_scores(root, inputs)
    else:
        assert list(runner.frozen_scores(root, inputs).values()) == [D(".6")]


def test_model_never_silently_fills_missing_scores():
    bars = setup()
    with pytest.raises(ValueError, match="누락"):
        runner.simulate(
            bars, bars[200].open_time, "ensemble-mlp", backtest.ExitPolicy(), costs(), 0, {}
        )


def test_study_preserves_original_and_reports_each_domain(tmp_path, monkeypatch):
    bars = setup()
    start, end = bars[200].open_time, bars[-1].close_time
    root = tmp_path / "inputs"
    scores = {b.close_time: D(".6") for b in bars if b.close_time >= start}
    monkeypatch.setattr(runner, "frozen_scores", lambda root, inputs: scores)
    locations = {
        "breakout-v1": "breakout-entry-cadence-67/seed-17/continuous",
        "wick-25": "breakout-wick-28/seed-17/continuous",
        "entry-stop-trend-context": "breakout-entry-stop-trend-context-51/seed-17/continuous",
        "liquidation-buffer": "breakout-entry-cadence-67/seed-17/continuous",
        "ensemble-mlp": "breakout-ensemble-23/candidate/seed-17/continuous",
    }
    for reference in ("breakout-entry-cadence-67", "historical-window-70"):
        write_json(root / reference / "status.json", {"status": "completed_partial_coverage"})
        write_json(
            root / reference / "intervals.json",
            [{"start": start.isoformat(), "end": end.isoformat()}],
        )
        write_json(root / reference / "datasets/block-000/quality.json", {})
    monkeypatch.setattr(runner, "load_dataset", lambda p: (bars, "test"))
    for scenario, fee, slip, delay in runner.SCENARIOS:
        for model, location in locations.items():
            r = runner.simulate(
                bars, start, model, backtest.ExitPolicy(), costs(fee, slip), delay, scores
            )
            write_json(
                root / location / f"block-000/{scenario}.{model}.json", r.model_dump(mode="json")
            )
            if model in ("breakout-v1", "liquidation-buffer"):
                write_json(
                    root / f"historical-window-70/accounts/block-000/{scenario}.{model}.json",
                    r.model_dump(mode="json"),
                )
    output = tmp_path / "result"
    runner.run_study(root, output)
    status = json.loads((output / "status.json").read_text())
    assert status["pnl_checks"] == 180 and status["original_json_matches"] == 28
    results = json.loads((output / "results.json").read_text())
    assert len(results["recent"]["ranking"]) == 25
    assert len(results["historical"]["ranking"]) == 20
    assert not status["live_enabled"]
    with pytest.raises(FileExistsError):
        runner.run_study(root, output)
    original_read = runner.Inputs.read
    target = root / locations["breakout-v1"] / "block-000/base.breakout-v1.json"
    monkeypatch.setattr(
        runner.Inputs,
        "read",
        lambda self, p, *args: {} if p == target else original_read(self, p, *args),
    )
    bad = tmp_path / "bad"
    with pytest.raises(ValueError, match="재현"):
        runner.run_study(root, bad)
    assert (bad / "failure.json").exists() and not (bad / "status.json").exists()


def test_explicit_default_reproduces_buffer():
    from test_breakout_entry_stop_trend_context import MODES

    bars = path()
    for delay in (0, 1):
        old = simulate(bars, delay=delay)
        new = backtest.run_backtest(
            bars,
            bars[200].open_time,
            D(1000000),
            costs(),
            "breakout-v1",
            extra_delay_bars=delay,
            breakout_liquidation_buffer=True,
            exit_policy=backtest.ExitPolicy(),
            **MODES,
        )
        assert old.model_dump() == new.model_dump()


@pytest.mark.parametrize("limit", [".1", ".15", ".2"])
def test_drawdown_boundary_and_budget_use_same_limit(limit):
    p = backtest._Portfolio(D(1000000), costs(), True, drawdown_limit=D(limit))
    p.cash = D(1000000) * (1 - D(limit)) + D(1)
    p.mark(D(1), setup()[0].open_time, "close")
    assert not p.halted
    p.cash -= 1
    p.mark(D(1), setup()[0].close_time, "close")
    assert p.halted
    strict = backtest._Portfolio(D(1000000), costs(), True)
    relaxed = backtest._Portfolio(D(1000000), costs(), True, drawdown_limit=D(".2"))
    assert not strict.channel_entry_fits(D(100), D(85))
    assert relaxed.channel_entry_fits(D(100), D(85))


@pytest.mark.parametrize("delay", [0, 1])
def test_wider_fixed_stop_keeps_original_signal_range_and_delay(delay):
    bars = setup()
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": D(97), "low": D(1)})
    args = dict(
        breakout_entry_stop=True, breakout_entry_stop_confirmations=2, extra_delay_bars=delay
    )
    narrow = backtest.run_backtest(
        bars, bars[200].open_time, D(1000000), costs(), "breakout-v1", **args
    )
    wide = backtest.run_backtest(
        bars,
        bars[200].open_time,
        D(1000000),
        costs(),
        "breakout-v1",
        exit_policy=backtest.ExitPolicy(drawdown_limit=D(".2"), stop_multiple=D(6)),
        **args,
    )
    assert narrow.fills[1].time == bars[205 + delay].open_time
    assert wide.fills[1].time > narrow.fills[1].time
    assert wide.fills[0] == narrow.fills[0]


@pytest.mark.parametrize(
    "bad",
    [
        dict(drawdown_limit="NaN"),
        dict(drawdown_limit=0),
        dict(drawdown_limit=".3"),
        dict(stop_multiple=2),
        dict(stop_multiple="Infinity"),
        dict(channel_hours=49),
    ],
)
def test_invalid_policy_is_rejected(bad):
    with pytest.raises(ValueError):
        backtest.ExitPolicy(**bad)


def test_policy_rejected_for_non_breakout_strategy():
    bars = setup()
    with pytest.raises(ValueError, match="돌파"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            D(1000000),
            costs(),
            "cash",
            exit_policy=backtest.ExitPolicy(drawdown_limit=D(".15")),
        )
