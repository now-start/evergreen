import json
from decimal import Decimal as D

import pytest
from test_breakout_liquidation_buffer import path

from evergreen.market import write_json
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.strategy_family import simulate


@pytest.mark.parametrize("fee,slip", [(1, 1), (0, 1), (1, 0), (0, 0)])
def test_decomposition_keeps_fixed_fills_separate_from_rerun(fee, slip):
    from evergreen.research.experiments.cost_path import compare

    bars = path()
    start = bars[200].open_time
    base = simulate(bars, start, "liquidation-buffer", costs(), 0)
    changed = simulate(bars, start, "liquidation-buffer", costs(fee, slip), 0)
    assert base.fills
    row = compare(base, changed, fee, slip)
    fixed = (
        base.final_equity
        - base.initial_capital
        + (1 - fee) * base.total_fees
        + (1 - slip) * base.total_slippage
    )
    assert D(row["fixed_fill_pnl"]) == fixed
    assert abs(
        D(row["path_effect"]) + fixed - (changed.final_equity - changed.initial_capital)
    ) < D(".000001")
    if fee == slip == 1:
        assert D(row["path_effect"]) == 0 and not row["schedule_changed"]
    with pytest.raises(ValueError, match="조건"):
        compare(base, changed.model_copy(update={"extra_delay_bars": 1}), fee, slip)
    with pytest.raises(ValueError, match="비용"):
        compare(base, changed, 2, slip)


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_frozen_baseline_and_no_promotion(tmp_path, monkeypatch, corrupt):
    from evergreen.research.experiments import cost_path as runner

    bars = path()
    start = bars[200].open_time

    def blocks(raw, output):
        output.mkdir()
        write_json(output / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(runner, "contiguous_blocks", blocks)
    ref = tmp_path / "reference"
    group = ref / "seed-17/continuous/block-000"
    group.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "62"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for model in runner.MODELS:
        data = simulate(bars, start, model, costs(), 0).model_dump(mode="json")
        if corrupt:
            data["total_fees"] = "123"
        write_json(group / f"base.{model}.json", data)
    out = tmp_path / "output"
    if corrupt:
        with pytest.raises(ValueError, match="대조"):
            runner.run_study(tmp_path, ref, out)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out)
        status = json.loads((out / "status.json").read_text())
        assert status["control_comparisons"] == 4 and status["pnl_checks"] == 16
        assert not status["live_enabled"] and not status["promotion_candidate"]
        for model in runner.MODELS:
            data = json.loads(
                (out / f"seed-17/continuous/block-000/zero-both.{model}.json").read_text()
            )
            assert data == simulate(bars, start, model, costs(0, 0), 0).model_dump(mode="json")
        with pytest.raises(FileExistsError):
            runner.run_study(tmp_path, ref, out)


def test_sizing_effect_is_not_misreported_as_schedule_change():
    from evergreen.research.experiments.cost_path import compare, summarize

    bars = path()
    start = bars[200].open_time
    original = simulate(bars, start, "liquidation-buffer", costs(), 0)
    exit_time = original.fills[1].time
    index = next(i for i, b in enumerate(bars) if b.open_time == exit_time)
    bars[index] = bars[index].model_copy(update={"open": D(102), "low": D(101)})
    base = simulate(bars, start, "liquidation-buffer", costs(), 0)
    changed = simulate(bars, start, "liquidation-buffer", costs(0, 0), 0)
    row = compare(base, changed, 0, 0)
    assert not row["schedule_changed"]
    assert D(row["path_effect"]) != 0
    assert D(row["fixed_fill_pnl"]) != 0
    assert summarize([row])["path_effect"] == row["path_effect"]
    with pytest.raises(ValueError, match="빈"):
        summarize([])
    with pytest.raises(ValueError, match="비용 합"):
        compare(base.model_copy(update={"total_fees": base.total_fees + D(1)}), changed, 0, 0)
