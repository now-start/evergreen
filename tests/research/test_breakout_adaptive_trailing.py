import json
from decimal import Decimal

import pytest
from test_breakout_profit_trailing import profit_bars

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def adaptive_bars():
    bars = profit_bars()
    # Prior24 TR at232=3.263541666..., distance9.790625, peak111.125.
    bars[232] = bars[232].model_copy(update={"close": Decimal("101.334375"), "low": Decimal(101)})
    # At233 priorTR=3.346875, exit threshold101.084375. Current high is excluded.
    bars[233] = bars[233].model_copy(
        update={"close": Decimal("101.05"), "low": Decimal(100), "high": Decimal(10000)}
    )
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_adaptive_distance_excludes_current_bar_retains_activation_and_boundary(delay):
    bars = adaptive_bars()
    if delay:
        bars[200] = bars[200].model_copy(update={"close": Decimal(120)})
    args = (bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    options = {
        "breakout_trailing_exit": True,
        "breakout_trailing_profit_only": True,
        "extra_delay_bars": delay,
    }
    fixed = run_backtest(*args, **options)
    assert fixed == run_backtest(*args, **options, breakout_trailing_adaptive=False)
    result = run_backtest(*args, **options, breakout_trailing_adaptive=True)
    assert result.fills[0] == fixed.fills[0]
    assert fixed.fills[1].time == bars[233 + delay].open_time
    assert result.fills[1].time == bars[234 + delay].open_time
    assert result.fills[1].reason == "signal" and not result.halted
    # Peak111.125 activates at frozen104+7.125, despite dynamic distance exceeding7.125.
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(100000)})
    assert (
        run_backtest(*args, **options, breakout_trailing_adaptive=True).fills[:2]
        == result.fills[:2]
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_adaptive_distance_never_shrinks_below_entry_range(delay):
    bars = profit_bars()
    for i in range(200, 230):
        bars[i] = bars[i].model_copy(
            update=dict.fromkeys(("open", "high", "low", "close"), Decimal(104))
        )
    bars[230] = bars[230].model_copy(update={"high": Decimal("111.125"), "low": Decimal(104)})
    bars[231] = bars[231].model_copy(update={"high": Decimal(104), "low": Decimal(104)})
    bars[232] = bars[232].model_copy(update={"high": Decimal(104), "low": Decimal(103)})
    args = (bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    options = {
        "breakout_trailing_exit": True,
        "breakout_trailing_profit_only": True,
        "extra_delay_bars": delay,
    }
    fixed = run_backtest(*args, **options)
    adaptive = run_backtest(*args, **options, breakout_trailing_adaptive=True)
    assert adaptive.fills[:2] == fixed.fills[:2]
    assert adaptive.fills[1].time == bars[233 + delay].open_time


def test_adaptive_requires_profit_activation_and_explicit_candidate(tmp_path):
    bars = profit_bars()
    with pytest.raises(ValueError, match="가변"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_trailing_exit=True,
            breakout_trailing_adaptive=True,
        )
    with pytest.raises(ValueError, match="동시"):
        breakout_confirmation.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            profit_trailing_exit=True,
            adaptive_trailing_exit=True,
        )
    with pytest.raises(ValueError, match="가변"):
        evaluate(
            {"breakout-v1": [], "cash": [], "prior": [], "candidate": []},
            tmp_path,
            models=("candidate",),
            trailing_exit_models=("candidate",),
            trailing_adaptive_models=("candidate",),
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_adaptive_pipeline_preserves_profit_trailing_control(tmp_path, monkeypatch, corrupt):
    bars = adaptive_bars()
    start = bars[200].open_time

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(breakout_confirmation, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    folder = ref / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "35"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "profit-trailing-tr24"):
            filtered = name != "breakout-v1"
            r = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes=dict.fromkeys((b.close_time for b in bars if b.close_time >= start), 2)
                if filtered
                else None,
                regime_policy="breakout-filter" if filtered else "routing",
                breakout_trailing_exit=filtered,
                breakout_trailing_profit_only=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                r["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", r)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            breakout_confirmation.run_study(tmp_path, ref, out, adaptive_trailing_exit=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        breakout_confirmation.run_study(tmp_path, ref, out, adaptive_trailing_exit=True)
        r = json.loads(
            (out / "seed-17/continuous/block-000/base.adaptive-trailing-tr24.json").read_text()
        )
        assert r["fills"][1]["time"] == bars[234].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "36"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
        assert len(list((out / "seed-17/continuous/block-000").glob("*.json"))) == 21
