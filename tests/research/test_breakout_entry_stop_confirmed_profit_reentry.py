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
        breakout_entry_stop_loss_reset=True,
        breakout_confirmed_profit_reentry=enabled,
        extra_delay_bars=delay,
    )


def profitable_sale():
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"open": Decimal(105), "high": Decimal(106)})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_two_independent_breakouts_and_pending_fallback(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    if delay:
        bars[206] = bars[206].model_copy(update={"open": Decimal(105), "high": Decimal(106)})
    for i, price in ((210, 112), (211, 114)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})
    result = simulate(bars, delay=delay)
    assert result.fills[0].time == bars[200 + delay].open_time
    assert result.fills[2].signal_time == bars[211].close_time
    assert result.fills[2].time == bars[212 + delay].open_time
    assert simulate(bars, enabled=False, delay=delay).fills[2].time == bars[211 + delay].open_time


@pytest.mark.parametrize("close", [Decimal(112), Decimal("113.113")])
def test_second_bar_uses_own_ceiling_and_equality_resets(monkeypatch, close):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    bars[210] = bars[210].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    bars[211] = bars[211].model_copy(update={"close": close, "high": Decimal(114)})
    for i, price in ((212, 115), (213, 117)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})
    result = simulate(bars)
    assert result.fills[2].time == bars[214].open_time
    assert result.fills[2].signal_time == bars[213].close_time


def test_pre_sell_bar_is_not_confirmation_and_actual_sell_bar_can_count(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    for i, price in ((205, 112), (206, 114), (207, 116)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})
    bars[206] = bars[206].model_copy(update={"open": Decimal(105)})
    result = simulate(bars, delay=1)
    assert result.fills[1].time == bars[206].open_time
    assert result.fills[2].signal_time == bars[207].close_time
    assert result.fills[2].time == bars[209].open_time
    assert simulate(bars).fills[2].signal_time == bars[206].close_time


@pytest.mark.parametrize("reset", [False, True])
def test_strict_channel_reset_restores_single_bar_entry(monkeypatch, reset):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    bars[249] = bars[249].model_copy(
        update={"close": Decimal(100 if reset else 101), "low": Decimal(99)}
    )
    bars[250] = bars[250].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    result = simulate(bars)
    if reset:
        assert result.fills[2].time == bars[251].open_time
    else:
        assert len(result.fills) == 2


def test_net_loss_keeps_full_lock_but_channel_sale_needs_no_confirmation(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    bars[205] = bars[205].model_copy(update={"open": Decimal("104.1")})
    for i, price in ((210, 112), (211, 114)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})
    assert len(simulate(bars).fills) == 2
    bars = setup()
    bars[160] = bars[160].model_copy(update={"low": Decimal(98)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    bars[203] = bars[203].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    bars[204] = bars[204].model_copy(update={"open": Decimal(105)})
    bars[210] = bars[210].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    assert simulate(bars).fills[2].time == bars[211].open_time
    assert simulate(bars) == simulate(bars, enabled=False)


def test_rejected_sale_waits_for_actual_sale_before_confirming_and_risk_wins(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    for i in (208, 209):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    for i, price in ((210, 112), (211, 114)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})
    bars[210] = bars[210].model_copy(update={"open": Decimal(105)})
    result = simulate(bars, capital=5100)
    assert result.rejections[0].time == bars[205].open_time
    assert result.fills[1].time == bars[210].open_time
    assert result.fills[2].time == bars[212].open_time
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.halted and len(result.fills) == 2 and result.fills[1].reason == "risk"


def test_confirmation_requires_loss_reset_and_declared_models(tmp_path):
    bars = profit_path()
    with pytest.raises(ValueError, match="수익 재진입"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_confirmed_profit_reentry=True,
        )
    with pytest.raises(ValueError, match="수익 재진입"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            confirmed_profit_reentry_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_loss_reset=True,
            confirmed_profit_reentry=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_loss_reset_control(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profitable_sale()
    start = bars[200].open_time
    for i, price in ((210, 112), (211, 114)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})

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
    write_json(ref / "protocol.json", {"experiment": "47"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-loss-reset"):
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
                breakout_entry_stop_loss_reset=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, confirmed_profit_reentry=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, confirmed_profit_reentry=True)
        result = json.loads(
            (
                out / "seed-17/continuous/block-000/base.entry-stop-confirmed-profit-reentry.json"
            ).read_text()
        )
        assert result["fills"][2]["signal_time"] == bars[211].close_time.isoformat().replace(
            "+00:00", "Z"
        )
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "48"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
