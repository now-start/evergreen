import hashlib
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from test_breakout_liquidation_buffer import path

from evergreen.market import write_json
from evergreen.research.experiments import delay_path as runner
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.strategy_family import simulate


def pair():
    bars = path()
    return [simulate(bars, bars[200].open_time, "breakout-v1", costs(), d) for d in (0, 1)]


def test_execution_delay_alone_is_not_signal_divergence():
    base, delayed = pair()
    row = runner.compare(base, delayed)
    assert row["first_signal_divergence"] is None
    assert row["common_signal_prefix"] == len(base.fills)
    assert row["matched_prefix_execution_lags_hours"] == [1.0, 0.0]
    assert D(row["pnl_change"]) == delayed.final_equity - base.final_equity


@pytest.mark.parametrize("change", ["time", "reason", "missing"])
def test_first_changed_signal_or_missing_fill_is_reported(change):
    base, delayed = pair()
    fills = list(delayed.fills)
    if change == "time":
        fills[0] = fills[0].model_copy(
            update={"signal_time": fills[0].signal_time + timedelta(hours=1)}
        )
    elif change == "reason":
        fills[0] = fills[0].model_copy(update={"reason": "risk"})
    else:
        fills = []
    altered = delayed.model_copy(update={"fills": fills})
    difference = runner.signal_difference(base, altered)
    assert difference["first_signal_divergence"]["index"] == 0
    assert difference["common_signal_prefix"] == 0
    assert (
        difference["first_signal_divergence"]["delayed"] is None
        if change == "missing"
        else difference["first_signal_divergence"]["delayed"] is not None
    )


@pytest.mark.parametrize(
    "change", [{"extra_delay_bars": 0}, {"costs": costs(2, 2)}, {"net_return": D(".9")}]
)
def test_pair_contract_rejects_invalid_delay_cost_or_equity(change):
    base, delayed = pair()
    with pytest.raises(ValueError):
        runner.compare(base, delayed.model_copy(update=change))


def test_relative_delay_change_is_difference_of_two_paired_differences():
    groups = {
        name: [{"account": "a", "pnl_change": str(value)}]
        for name, value in (("breakout-v1", 30), ("liquidation-buffer", 10))
    }
    assert runner.relative_changes(groups) == [{"account": "a", "relative_delay_change": "-20"}]
    groups["liquidation-buffer"][0]["account"] = "b"
    with pytest.raises(ValueError):
        runner.relative_changes(groups)


def reference(tmp_path, monkeypatch):
    bars = path()
    bars[200] = bars[200].model_copy(
        update={"open": D(120), "high": D(120), "close": D(100), "low": D(99)}
    )
    source = tmp_path / "source"
    directory = source / "seed-17/continuous/block-000"
    directory.mkdir(parents=True)
    dataset = source / "datasets/block-000"
    dataset.mkdir(parents=True)
    inputs = {}
    for name in ("candles.jsonl", "quality.json"):
        write_json(dataset / name, [])
        inputs[str((dataset / name).resolve())] = hashlib.sha256(
            (dataset / name).read_bytes()
        ).hexdigest()
    monkeypatch.setattr(runner, "load_dataset", lambda directory: (bars, "fixture"))
    write_json(source / "protocol.json", {"experiment": "67"})
    write_json(source / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        source / "intervals.json",
        [{"start": bars[200].open_time.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for name in runner.MODELS:
        for s, delay in runner.SCENARIOS:
            result = simulate(
                bars,
                bars[200].open_time,
                "liquidation-buffer" if name == runner.CADENCE else name,
                costs(),
                delay,
                entry_cadence_hours=4 if name == runner.CADENCE else 1,
            )
            f = directory / f"{s}.{name}.json"
            write_json(f, result.model_dump(mode="json"))
            inputs[str(f.resolve())] = hashlib.sha256(f.read_bytes()).hexdigest()
    write_json(source / "inputs.json", inputs)
    return source


@pytest.mark.parametrize("damage", [None, "hash", "missing", "replay"])
def test_all_changed_halt_pairs_replayed_and_fail_closed(tmp_path, monkeypatch, damage):
    source = reference(tmp_path, monkeypatch)
    if damage == "hash":
        with (source / "seed-17/continuous/block-000/base.breakout-v1.json").open("a") as f:
            f.write(" ")
    elif damage == "missing":
        (source / "seed-17/continuous/block-000/delay-1h.breakout-v1.json").unlink()
    elif damage == "replay":
        original = runner.simulate
        monkeypatch.setattr(
            runner,
            "simulate",
            lambda *a, **kw: original(*a, **kw).model_copy(update={"total_fees": D(123)}),
        )
    out = tmp_path / "out"
    if damage:
        with pytest.raises((ValueError, OSError)):
            runner.run_study(source, out)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(source, out)
        r = json.loads((out / "results.json").read_text())
        status = json.loads((out / "status.json").read_text())
        changed = [(n, a) for n, rows in r["models"].items() for a in rows if a["halt_changed"]]
        assert changed
        assert status["checked_results"] == 8
        assert status["replayed_results"] == 2 * len(changed)
        for _name, a in changed:
            assert len(a["risk_replay"]) == 2
            assert (a["risk_replay"]["base"] is not None) == a["base_halted"]
            assert (a["risk_replay"]["delay-1h"] is not None) == a["delayed_halted"]
        assert not status["promotion_candidate"] and not status["live_enabled"]
        with pytest.raises(FileExistsError):
            runner.run_study(source, out)
