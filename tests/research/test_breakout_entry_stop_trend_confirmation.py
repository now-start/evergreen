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
        breakout_entry_stop_adaptive_trail=True,
        breakout_entry_stop_trend_confirmation=enabled,
        extra_delay_bars=delay,
    )


def declining_path():
    bars = profit_path()
    for i in range(155, 179):
        bars[i] = bars[i].model_copy(update={"close": Decimal(102), "high": Decimal(102)})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_rising_trend_defers_profit_exit_but_not_entry(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    control, candidate = simulate(bars, enabled=False, delay=delay), simulate(bars, delay=delay)
    assert control.fills[0] == candidate.fills[0]
    assert control.fills[1].time == bars[205 + delay].open_time
    assert candidate.fills[1].time > control.fills[1].time
    assert not candidate.halted


@pytest.mark.parametrize("delay", [0, 1])
def test_nonrising_trend_preserves_confirmed_exit_and_pending_order(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    bars[205] = bars[205].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    result = simulate(bars, delay=delay)
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].time == bars[205 + delay].open_time


def test_current_high_is_not_a_trend_input(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    result = simulate(bars)
    assert result.fills[1].signal_time == bars[204].close_time
    bars[203] = bars[203].model_copy(update={"high": Decimal(500)})
    assert simulate(bars).fills[:2] == result.fills[:2]


def test_equal_trend_qualifies_and_intervening_rise_resets(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    for i in range(155, 176):
        bars[i] = bars[i].model_copy(update={"close": Decimal(101)})
    assert sum(b.close for b in bars[179:203]) == sum(b.close for b in bars[155:179])
    # Equality at 203 qualifies, but 204 rises and must reset instead of selling.
    assert simulate(bars).fills[1].signal_time != bars[204].close_time
    # Raise only the preceding window of 204: 203 equality must now count.
    bars[155] = bars[155].model_copy(update={"close": Decimal(96), "low": Decimal(95)})
    bars[156] = bars[156].model_copy(update={"close": Decimal(106), "high": Decimal(106)})
    # Preserve original entry despite the fixture's higher historical high.
    bars[199] = bars[199].model_copy(update={"close": Decimal(107), "high": Decimal(108)})
    # New signal close changes the prior window by four; balance the old window.
    bars[157] = bars[157].model_copy(update={"close": Decimal(105), "high": Decimal(105)})
    assert sum(b.close for b in bars[179:203]) == sum(b.close for b in bars[155:179])
    # A shifted window including the signal close would incorrectly block 204.
    assert sum(b.close for b in bars[181:205]) > sum(b.close for b in bars[157:181])
    assert simulate(bars).fills[1].signal_time == bars[204].close_time


@pytest.mark.parametrize("activated", [False, True])
def test_initial_stop_bypasses_rising_trend_even_after_profit(monkeypatch, activated):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(1 if activated else 2))
    bars = profit_path() if activated else setup()
    # Use a narrower initial stop to keep this distinct from the account risk rule.
    if activated:
        bars[202] = bars[202].model_copy(update={"close": Decimal(107)})
    for i in (203, 204):
        close = Decimal("100.9" if activated else "97")
        bars[i] = bars[i].model_copy(update={"close": close, "low": close - 1})
    result = simulate(bars)
    assert not result.halted and result.fills[1].reason == "signal"
    assert result.fills[1].signal_time == bars[204].close_time


def test_channel_bypasses_trend_and_new_buy_resets_peak(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = setup()
    bars[160] = bars[160].model_copy(update={"low": Decimal(99)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    bars[203] = bars[203].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    result = simulate(bars)
    assert result.fills[1].signal_time == bars[203].close_time
    assert result.fills[1].reason == "signal" and not result.halted
    bars = declining_path()
    bars[249] = bars[249].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    result = simulate(bars)
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[-1].reason == "settlement"


def test_no_retroactive_breaches_and_ratchet_survives_trend_deferral(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    for i in range(203, 246):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    qualifying = [
        i
        for i in range(203, 246)
        if sum(b.close for b in bars[i - 24 : i]) <= sum(b.close for b in bars[i - 48 : i - 24])
    ]
    first = qualifying[0]
    assert first > 204 and qualifying[1] == first + 1
    result = simulate(bars)
    assert result.fills[1].signal_time == bars[first + 1].close_time
    assert result.fills[1].time == bars[first + 2].open_time


def test_rejected_sell_retains_state_zero_tr_and_risk_precedence(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    # Keep the old window above the recent one through the retry signal.
    for i in range(160, 165):
        bars[i] = bars[i].model_copy(update={"close": Decimal(104), "high": Decimal(104)})
    bars[199] = bars[199].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    for i in (208, 209):
        bars[i] = bars[i].model_copy(update={"close": Decimal(101), "low": Decimal(100)})
    result = simulate(bars, capital=5100)
    assert result.rejections[0].time == bars[205].open_time
    assert result.fills[1].time == bars[210].open_time
    bars[205] = bars[205].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.halted and result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[207].open_time
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(0))
    assert simulate(profit_path()) == simulate(profit_path(), enabled=False)


def test_trend_requires_adaptive_mode_without_reentry_variants(tmp_path):
    bars = setup()
    for options in (
        {},
        {"breakout_entry_stop_adaptive_trail": True, "breakout_entry_stop_loss_reset": True},
    ):
        with pytest.raises(ValueError, match="추세 확인"):
            backtest.run_backtest(
                bars,
                bars[200].open_time,
                Decimal(1000000),
                costs(),
                "breakout-v1",
                breakout_entry_stop_trend_confirmation=True,
                **options,
            )
    with pytest.raises(ValueError, match="추세 확인"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_trend_confirmation_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_trend_confirmation=True,
            confirmed_profit_reentry=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_experiment46_control(tmp_path, monkeypatch, corrupt):
    bars = profit_path()
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
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
    write_json(ref / "protocol.json", {"experiment": "46"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-adaptive-trail-reset"):
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
                breakout_entry_stop_adaptive_trail=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_trend_confirmation=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_trend_confirmation=True)
        result = json.loads(
            (
                out / "seed-17/continuous/block-000/base.entry-stop-trend-confirmation.json"
            ).read_text()
        )
        assert result["fills"][1]["time"] > bars[205].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "49"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
