import hashlib
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from test_breakout_liquidation_buffer import path

from evergreen.market import write_json
from evergreen.research.experiments import account_sensitivity as runner
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.research.experiments.strategy_family import simulate, summarize


def results(returns):
    bars = path()
    base = simulate(bars, bars[200].open_time, "cash", costs(), 0)
    return [
        base.model_copy(
            update={
                "start": base.start + timedelta(days=10 * i),
                "end": base.end + timedelta(days=10 * i),
                "net_return": D(value),
                "final_equity": base.initial_capital * (1 + D(value)),
            }
        )
        for i, value in enumerate(returns)
    ]


def test_even_odd_paired_median_and_every_single_omission():
    candidate = results(["0.1", "0.3", "-0.2", "0.2"])
    control = results(["0", "0", "0", "0"])
    actual = runner.sensitivity(["a", "b", "c", "d"], candidate, control)
    assert D(actual["full"]["paired_median_return"]) == D(".15")
    assert D(actual["full"]["paired_total_pnl"]) == D(400000)
    assert actual["full"]["positive_accounts"] == 3
    assert [r["excluded"] for r in actual["leave_one_out"]] == ["a", "b", "c", "d"]
    assert [D(r["paired_median_return"]) for r in actual["leave_one_out"]] == [
        D(".2"),
        D(".1"),
        D(".2"),
        D(".1"),
    ]
    assert [D(r["paired_total_pnl"]) for r in actual["leave_one_out"]] == [
        D(300000),
        D(100000),
        D(600000),
        D(200000),
    ]
    assert all(r["accounts"] == 3 and "gate" not in r for r in actual["leave_one_out"])
    assert actual["median_range"]["min_excluded"] == ["b", "d"]
    assert actual["rows"][0]["hours"] == 60


def test_sign_change_including_zero_and_paired_not_difference_of_medians():
    candidate = results([".1", ".1", ".5"])
    control = results(["0", ".5", ".1"])
    actual = runner.sensitivity(["a", "b", "c"], candidate, control)
    assert D(actual["full"]["paired_median_return"]) == D(".1")
    assert actual["median_sign_counts"] == {"positive": 1, "zero": 1, "negative": 1}
    assert actual["pnl_sign_counts"] == {"positive": 1, "zero": 1, "negative": 1}
    assert actual["median_sign_changes"] == 2


@pytest.mark.parametrize(
    "case", ["duplicate", "count", "one", "boundary", "cost", "delay", "order", "return"]
)
def test_invalid_account_pairs_fail(case):
    a, b = results(["0", ".1", ".2"]), results(["0", "0", "0"])
    ids = ["a", "b", "c"]
    if case == "duplicate":
        ids[1] = "a"
    elif case == "count":
        b.pop()
    elif case == "one":
        ids, a, b = ids[:1], a[:1], b[:1]
    elif case == "boundary":
        b[0] = b[0].model_copy(update={"end": b[0].end + timedelta(hours=1)})
    elif case == "cost":
        b[0] = b[0].model_copy(update={"costs": costs(2, 2)})
    elif case == "delay":
        b[0] = b[0].model_copy(update={"extra_delay_bars": 1})
    elif case == "order":
        a.reverse()
        b.reverse()
    else:
        a[0] = a[0].model_copy(update={"net_return": D(".5")})
    with pytest.raises(ValueError):
        runner.sensitivity(ids, a, b)


def reference(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    bars = path()
    intervals = []
    inputs = {}
    groups = {s: {m: [] for m in runner.MODELS} for s, *_ in SCENARIOS}
    for i in range(3):
        shifted = [
            b.model_copy(update={"open_time": b.open_time + timedelta(days=i * 10)}) for b in bars
        ]
        start, end = shifted[200].open_time, shifted[-1].close_time
        intervals.append({"start": start.isoformat(), "end": end.isoformat()})
        directory = ref / f"seed-17/continuous/block-{i:03}"
        directory.mkdir(parents=True)
        for s, fee, slip, delay in SCENARIOS:
            for name in runner.MODELS:
                result = simulate(
                    shifted,
                    start,
                    "liquidation-buffer" if name == runner.CADENCE else name,
                    costs(fee, slip),
                    delay,
                    entry_cadence_hours=4 if name == runner.CADENCE else 1,
                )
                f = directory / f"{s}.{name}.json"
                write_json(f, result.model_dump(mode="json"))
                inputs[str(f.resolve())] = hashlib.sha256(f.read_bytes()).hexdigest()
                groups[s][name].append(result)
    write_json(ref / "inputs.json", inputs)
    write_json(ref / "intervals.json", intervals)
    write_json(ref / "protocol.json", {"experiment": "67"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "results.json",
        {
            "scenarios": {
                s: {m: summarize(rows, g["breakout-v1"], s) for m, rows in g.items()}
                for s, g in groups.items()
            }
        },
    )
    return ref


@pytest.mark.parametrize("damage", [None, "hash", "missing", "summary", "interval"])
def test_pipeline_verifies_inputs_and_does_not_regate(tmp_path, damage):
    ref = reference(tmp_path)
    if damage == "hash":
        with (ref / "seed-17/continuous/block-000/base.breakout-v1.json").open("a") as f:
            f.write(" ")
    elif damage == "missing":
        (ref / "seed-17/continuous/block-000/base.breakout-v1.json").unlink()
    elif damage == "summary":
        (ref / "results.json").write_text('{"scenarios": {}}')
    elif damage == "interval":
        intervals = json.loads((ref / "intervals.json").read_text())
        intervals.pop()
        (ref / "intervals.json").write_text(json.dumps(intervals))
    output = tmp_path / "output"
    if damage:
        with pytest.raises((ValueError, OSError, KeyError)):
            runner.run_study(ref, output)
        assert (output / "failure.json").exists() and not (output / "status.json").exists()
    else:
        runner.run_study(ref, output)
        status = json.loads((output / "status.json").read_text())
        report = json.loads((output / "results.json").read_text())
        assert status["checked_results"] == 48
        assert status["leave_one_out_comparisons"] == 48
        assert not status["promotion_candidate"] and not status["live_enabled"]
        assert (
            report["original_summaries"]
            == json.loads((ref / "results.json").read_text())["scenarios"]
        )
        with pytest.raises(FileExistsError):
            runner.run_study(ref, output)
