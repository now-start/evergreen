import json
from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def setup():
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[160] = bars[160].model_copy(update={"low": Decimal(80)})
    # Signal-bar TR must not enter the fixed distance; its low also separates channel exits.
    bars[199] = bars[199].model_copy(update={"low": Decimal(1)})
    return bars


def simulate(bars, *, enabled=True, delay=0, **kwargs):
    return run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_entry_stop=enabled,
        extra_delay_bars=delay,
        **kwargs,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_entry_stop_uses_signal_tr_actual_fill_and_strict_close(delay):
    bars = setup()
    if delay:
        bars[201] = bars[201].model_copy(update={"open": Decimal(108), "high": Decimal(109)})
    entry = bars[200 + delay].open
    assert prior_mean_true_range(bars[:200]) == 2
    stop = entry - 6
    bars[203] = bars[203].model_copy(update={"close": stop, "low": Decimal(1)})
    bars[204] = bars[204].model_copy(update={"close": stop - Decimal(".01"), "low": Decimal(1)})
    result = simulate(bars, delay=delay)
    assert result.fills[0].reference_price == entry
    assert result.fills[1].time == bars[205 + delay].open_time
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].reason == "signal"
    assert simulate(bars, enabled=False, delay=delay).fills[1].reason == "settlement"


def test_entry_stop_does_not_trail_price_or_recompute_distance():
    bars = setup()
    bars[201] = bars[201].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    bars[202] = bars[202].model_copy(update={"open": Decimal(110), "high": Decimal(111)})
    bars[203] = bars[203].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    result = simulate(bars)
    assert len(result.fills) == 2 and result.fills[1].reason == "settlement"


@pytest.mark.parametrize("wide", [False, True])
def test_zero_or_nonpositive_stop_does_not_add_exits(wide):
    bars = [
        b.model_copy(update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100)))
        for b in fixture()
    ]
    if wide:
        for i in range(175, 199):
            bars[i] = bars[i].model_copy(update={"high": Decimal(200), "low": Decimal(1)})
    entry = Decimal(204 if wide else 104)
    bars[199] = bars[199].model_copy(update={"close": entry - 1, "high": entry, "low": Decimal(80)})
    for i in range(200, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": entry, "close": entry, "high": entry + 1, "low": entry - 1}
        )
    bars[203] = bars[203].model_copy(update={"close": entry - 6, "low": entry - 7})
    assert simulate(bars) == simulate(bars, enabled=False)


def test_new_fill_resets_fixed_entry_stop_and_risk_still_wins():
    bars = setup()
    bars[204] = bars[204].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    bars[251] = bars[251].model_copy(
        update={
            "open": Decimal(112),
            "close": Decimal(112),
            "high": Decimal(113),
            "low": Decimal(111),
        }
    )
    stop = Decimal(112) - 3 * prior_mean_true_range(bars[:251])
    assert stop > 98
    bars[252] = bars[252].model_copy(
        update={"close": stop - Decimal(".01"), "high": Decimal(113), "low": Decimal(100)}
    )
    result = simulate(bars)
    assert [f.time for f in result.fills] == [bars[i].open_time for i in (200, 205, 251, 253)]
    bars[204] = bars[204].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    risk = simulate(bars)
    assert risk.halted and len(risk.fills) == 2 and risk.fills[1].reason == "risk"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"breakout_trailing_exit": True},
        {"breakout_failure_exit": True},
        {"breakout_exit_lookback": 24},
        {"breakout_cooldown_hours": 24},
        {"breakout_risk_budget": True},
        {"breakout_rejection_latch": True},
    ],
)
def test_entry_stop_rejects_other_modes(kwargs):
    with pytest.raises(ValueError, match="고정 손절"):
        simulate(setup(), **kwargs)


def test_entry_stop_rejects_unknown_candidate_and_cli_mix(tmp_path):
    with pytest.raises(ValueError, match="고정 손절"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior")},
            tmp_path,
            models=(),
            entry_stop_models=("cash",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path, tmp_path, tmp_path / "out", entry_stop=True, rejection_latch=True
        )
    assert not (tmp_path / "out").exists()


def test_entry_stop_skips_unfilled_orders_and_rejects_unrelated_strategy():
    bars = setup()
    args = (bars, bars[200].open_time, Decimal(1000), costs())
    result = run_backtest(*args, "breakout-v1", breakout_entry_stop=True)
    assert result.rejections and not result.fills
    with pytest.raises(ValueError, match="고정 손절"):
        run_backtest(*args, "cash", breakout_entry_stop=True)


@pytest.mark.parametrize("corrupt", [False, True])
def test_entry_stop_pipeline_isolates_latch_control(tmp_path, monkeypatch, corrupt):
    bars = setup()
    bars[204] = bars[204].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    start = bars[200].open_time

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(runner, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    folder = ref / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "39"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "expansion-rejection-latch"):
            filtered = name != "breakout-v1"
            result = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes=runner.expansion_schedule(bars, start) if filtered else None,
                regime_policy="breakout-filter" if filtered else "routing",
                breakout_rejection_latch=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.entry-stop-tr24.json").read_text()
        )
        assert result["fills"][0]["time"] == bars[200].open_time.isoformat().replace("+00:00", "Z")
        assert result["fills"][1]["time"] == bars[205].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "40"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
