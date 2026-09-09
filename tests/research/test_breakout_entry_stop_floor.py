import json
from decimal import Decimal

import pytest
from test_breakout_entry_stop import setup
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(bars, *, floor=True, delay=0, capital=1000000, **kwargs):
    return run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_floor=floor,
        extra_delay_bars=delay,
        **kwargs,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_floor_uses_frozen_prior168_tr_and_actual_delayed_open(delay):
    bars = setup()
    if delay:
        bars[201] = bars[201].model_copy(update={"open": Decimal(108), "high": Decimal(109)})
    slow = prior_mean_true_range(bars[:200], lookback=168)
    assert slow > prior_mean_true_range(bars[:200]) == 2
    entry = bars[200 + delay].open
    threshold = entry - 3 * slow
    bars[203] = bars[203].model_copy(
        update={"close": (threshold + entry - 6) / 2, "low": Decimal(1)}
    )
    bars[204] = bars[204].model_copy(update={"close": threshold, "low": Decimal(1)})
    bars[205] = bars[205].model_copy(
        update={"close": threshold - Decimal(".01"), "low": Decimal(1)}
    )
    actual = simulate(bars, delay=delay)
    assert actual.fills[0].reference_price == entry
    assert actual.fills[1].time == bars[206 + delay].open_time
    assert actual.fills[1].signal_time == bars[205].close_time
    assert actual.fills[1].reason == "signal"
    assert simulate(bars, floor=False, delay=delay).fills[1].time == bars[204 + delay].open_time
    # Future ranges cannot retroactively change the frozen stop or pending sell.
    bars[206] = bars[206].model_copy(update={"high": Decimal(500), "low": Decimal(1)})
    assert simulate(bars, delay=delay).fills[:2] == actual.fills[:2]


def test_short_tr_larger_than_slow_keeps_experiment40_behavior():
    bars = fixture()
    assert prior_mean_true_range(bars[:200]) > prior_mean_true_range(bars[:200], lookback=168)
    bars[203] = bars[203].model_copy(update={"close": Decimal(95), "low": Decimal(94)})
    assert simulate(bars) == simulate(bars, floor=False)


@pytest.mark.parametrize("slow_positive", [False, True])
def test_zero_short_tr_uses_nonzero_slow_or_disables_when_both_zero(slow_positive):
    bars = setup()
    for i in range(199):
        if not slow_positive or i >= 175:
            bars[i] = bars[i].model_copy(
                update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100))
            )
    assert prior_mean_true_range(bars[:200]) == 0
    slow = prior_mean_true_range(bars[:200], lookback=168)
    assert bool(slow) == slow_positive
    bars[203] = bars[203].model_copy(
        update={"close": bars[200].open - 3 * slow - Decimal(".01"), "low": Decimal(1)}
    )
    result = simulate(bars)
    if slow_positive:
        assert result.fills[1].time == bars[204].open_time
        assert simulate(bars, floor=False).fills[1].reason == "settlement"
    else:
        assert result == simulate(bars, floor=False)
        assert result.fills[1].reason == "settlement"


def test_nonpositive_threshold_does_not_force_exit():
    bars = setup()
    for i in range(31, 199):
        bars[i] = bars[i].model_copy(update={"high": Decimal(200), "low": Decimal(1)})
    bars[199] = bars[199].model_copy(update={"high": Decimal(204), "close": Decimal(203)})
    for i in range(200, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(204),
                "close": Decimal(204),
                "high": Decimal(205),
                "low": Decimal(203),
            }
        )
    bars[203] = bars[203].model_copy(update={"close": Decimal(198), "low": Decimal(197)})
    assert simulate(bars).fills[1].reason == "settlement"
    assert simulate(bars) == simulate(bars, floor=False)


def test_new_actual_entry_resets_floor_and_risk_has_priority():
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
    tr = max(prior_mean_true_range(bars[:251]), prior_mean_true_range(bars[:251], lookback=168))
    bars[252] = bars[252].model_copy(
        update={
            "close": Decimal(112) - 3 * tr - Decimal(".01"),
            "low": Decimal(1),
            "high": Decimal(113),
        }
    )
    result = simulate(bars)
    assert [f.time for f in result.fills] == [bars[i].open_time for i in (200, 205, 251, 253)]
    bars[204] = bars[204].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars)
    assert result.halted and len(result.fills) == 2 and result.fills[1].reason == "risk"
    unfilled = simulate(setup(), capital=1000)
    assert unfilled.rejections and not unfilled.fills


def test_floor_requires_entry_stop_and_candidate_subset(tmp_path):
    bars = setup()
    with pytest.raises(ValueError, match="하한"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop_floor=True,
        )
    with pytest.raises(ValueError, match="하한"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_floor_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path, tmp_path, tmp_path / "out", entry_stop=True, entry_stop_floor=True
        )
    assert not (tmp_path / "out").exists()
    with pytest.raises(ValueError, match="고정 손절"):
        simulate(bars, breakout_cooldown_hours=24)


def test_floor_requires_170_warmup_bars_before_any_order():
    bars = setup()
    with pytest.raises(ValueError, match="170 hours of warmup"):
        run_backtest(
            bars,
            bars[169].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop=True,
            breakout_entry_stop_floor=True,
        )
    # Original breakout and experiment40 retain their 169-bar contract.
    run_backtest(
        bars,
        bars[169].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_entry_stop=True,
    )


@pytest.mark.parametrize("corrupt", [False, True])
def test_floor_pipeline_keeps_experiment40_control(tmp_path, monkeypatch, corrupt):
    bars = setup()
    slow = prior_mean_true_range(bars[:200], lookback=168)
    bars[203] = bars[203].model_copy(
        update={"close": (Decimal(98) + Decimal(104) - 3 * slow) / 2, "low": Decimal(1)}
    )
    bars[205] = bars[205].model_copy(
        update={"close": Decimal(104) - 3 * slow - Decimal(".01"), "low": Decimal(1)}
    )
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
    write_json(ref / "protocol.json", {"experiment": "40"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-tr24"):
            filtered = name != "breakout-v1"
            result = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes={b.close_time: 2 for b in bars[199:]} if filtered else None,
                regime_policy="breakout-filter" if filtered else "routing",
                breakout_entry_stop=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_floor=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_floor=True)
        block = out / "seed-17/continuous/block-000"
        result = json.loads((block / "base.entry-stop-tr-floor.json").read_text())
        assert result["fills"][1]["time"] == bars[206].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "41"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
