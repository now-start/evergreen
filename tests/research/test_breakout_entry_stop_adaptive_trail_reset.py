import json
from decimal import Decimal

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_profit_trail_reset import profit_path

from evergreen.market import write_json
from evergreen.research import backtest
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(bars, *, enabled=True, delay=0, capital=1000000):
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_confirmations=2,
        breakout_entry_stop_channel_reset=True,
        breakout_entry_stop_profit_trail=True,
        breakout_entry_stop_adaptive_trail=enabled,
        extra_delay_bars=delay,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_observed_larger_tr_delays_profit_exit_without_changing_control(delay):
    bars = profit_path()
    assert simulate(bars, enabled=False, delay=delay).fills[1].time == bars[205 + delay].open_time
    assert simulate(bars, delay=delay).fills[1].time > bars[205 + delay].open_time


@pytest.mark.parametrize("delay", [0, 1])
def test_volatility_expansion_never_lowers_confirmed_threshold(monkeypatch, delay):
    bars = profit_path()
    # Isolate threshold state: 110 - 3*2 = 104 at 202, then TR jumps to 20.
    monkeypatch.setattr(
        backtest,
        "prior_mean_true_range",
        lambda h: Decimal(2 if h[-1].open_time <= bars[202].open_time else 20),
    )
    result = simulate(bars, delay=delay)
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].time == bars[205 + delay].open_time
    assert not result.halted


def test_current_bar_range_is_excluded_and_next_expansion_cannot_undo_breach():
    bars = profit_path()
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    bars[203] = bars[203].model_copy(update={"high": Decimal(500)})
    result = simulate(bars)
    assert result.fills[1].signal_time == bars[204].close_time


def test_equality_resets_and_pre_activation_stop_stays_fixed(monkeypatch):
    bars = profit_path()
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars[204] = bars[204].model_copy(update={"close": Decimal(104)})
    for i in (205, 206):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    assert simulate(bars).fills[1].signal_time == bars[206].close_time
    bars = setup()
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    monkeypatch.setattr(
        backtest, "prior_mean_true_range", lambda h: Decimal(2 if len(h) == 26 else 20)
    )
    assert simulate(bars).fills[1].signal_time == bars[204].close_time


def test_new_actual_buy_resets_ratchet_and_rejected_sell_preserves_it(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    for i in (208, 209):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    rejected = simulate(bars, capital=5100)
    assert rejected.rejections[0].time == bars[205].open_time
    assert rejected.fills[1].time == bars[210].open_time
    bars = profit_path()
    bars[249] = bars[249].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    for i in (251, 252):
        bars[i] = bars[i].model_copy(update={"open": Decimal(103), "close": Decimal(103)})
    result = simulate(bars)
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[-1].reason == "settlement"
    assert not simulate(bars, capital=1000).fills


def test_zero_tr_and_risk_order_precedence(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(0))
    assert simulate(profit_path()) == simulate(profit_path(), enabled=False)
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.halted and len(result.fills) == 2
    assert result.fills[1].reason == "risk" and result.fills[1].time == bars[207].open_time


def test_adaptation_requires_profit_trail_and_declared_models(tmp_path):
    bars = setup()
    with pytest.raises(ValueError, match="적응 추적"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop_adaptive_trail=True,
        )
    with pytest.raises(ValueError, match="적응 추적"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_adaptive_trail_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_profit_trail_reset=True,
            entry_stop_adaptive_trail_reset=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_frozen_profit_trail_control(tmp_path, monkeypatch, corrupt):
    bars = profit_path()
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
    write_json(ref / "protocol.json", {"experiment": "45"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-profit-trail-reset"):
            filtered = name != "breakout-v1"
            result = backtest.run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes={b.close_time: 2 for b in bars[199:]} if filtered else None,
                regime_policy="breakout-filter" if filtered else "routing",
                breakout_entry_stop=filtered,
                breakout_entry_stop_confirmations=2 if filtered else 1,
                breakout_entry_stop_channel_reset=filtered,
                breakout_entry_stop_profit_trail=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_adaptive_trail_reset=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_adaptive_trail_reset=True)
        result = json.loads(
            (
                out / "seed-17/continuous/block-000/base.entry-stop-adaptive-trail-reset.json"
            ).read_text()
        )
        assert result["fills"][1]["time"] != bars[205].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "46"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
