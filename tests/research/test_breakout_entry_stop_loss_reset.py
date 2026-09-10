import json
from decimal import Decimal

import pytest
from test_breakout_entry_stop_profit_trail_reset import profit_path

from evergreen.market import write_json
from evergreen.research import backtest
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(bars, *, enabled=True, delay=0, capital=1000000, charges=None):
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        charges or costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_confirmations=2,
        breakout_entry_stop_channel_reset=True,
        breakout_entry_stop_profit_trail=True,
        breakout_entry_stop_adaptive_trail=True,
        breakout_entry_stop_loss_reset=enabled,
        extra_delay_bars=delay,
    )


@pytest.mark.parametrize(
    "price,free,allow",
    [
        ("105", False, True),
        ("104.1", False, False),
        ("104", False, False),
        ("103", False, False),
        ("104", True, False),
        ("104.01", True, True),
    ],
)
def test_only_strict_net_profit_removes_lock(monkeypatch, price, free, allow):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"open": Decimal(price), "high": Decimal(106)})
    bars[210] = bars[210].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    result = simulate(bars, charges=costs(0, 0) if free else costs())
    buy, sell = result.fills[:2]
    pnl = sell.price * sell.quantity - sell.fee - buy.price * buy.quantity - buy.fee
    assert (pnl > 0) == allow
    if free and price == "104":
        assert pnl == 0
    if allow:
        assert result.fills[2].time == bars[211].open_time
    else:
        assert len(result.fills) == 2
    assert len(simulate(bars, enabled=False).fills) == 2


@pytest.mark.parametrize("delay", [0, 1])
def test_actual_fill_profit_not_signal_close_and_same_sell_bar_breakout(monkeypatch, delay):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    fill = 205 + delay
    bars[fill] = bars[fill].model_copy(
        update={"open": Decimal(105), "close": Decimal(112), "high": Decimal(113)}
    )
    result = simulate(bars, delay=delay)
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].time == bars[fill].open_time
    assert result.fills[2].signal_time == bars[fill].close_time
    assert result.fills[2].time == bars[fill + 1 + delay].open_time
    assert len(simulate(bars, enabled=False, delay=delay).fills) == 2


def test_new_loss_trade_locks_again_despite_positive_account_pnl(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"open": Decimal(108), "high": Decimal(109)})
    bars[210] = bars[210].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    for i in (212, 213):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    bars[214] = bars[214].model_copy(update={"open": Decimal(103)})
    bars[220] = bars[220].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    result = simulate(bars)
    assert [f.time for f in result.fills] == [bars[i].open_time for i in (200, 205, 211, 214)]
    assert result.final_equity > result.initial_capital and not result.halted


def test_rejection_preserves_state_until_real_sale_and_risk_stays_halted(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    for i in (208, 209):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    bars[210] = bars[210].model_copy(
        update={"open": Decimal(105), "close": Decimal(112), "high": Decimal(113)}
    )
    result = simulate(bars, capital=5100)
    assert result.rejections[0].time == bars[205].open_time
    assert (
        result.fills[1].time == bars[210].open_time and result.fills[2].time == bars[211].open_time
    )
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.halted and len(result.fills) == 2 and result.fills[1].reason == "risk"
    assert not simulate(bars, capital=1000).fills


def test_loss_reset_requires_adaptive_trail_and_declared_models(tmp_path):
    bars = profit_path()
    with pytest.raises(ValueError, match="손실 리셋"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop_loss_reset=True,
        )
    with pytest.raises(ValueError, match="손실 리셋"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_loss_reset_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_adaptive_trail_reset=True,
            entry_stop_loss_reset=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_adaptive_trail_control(tmp_path, monkeypatch, corrupt):
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
            runner.run_study(tmp_path, ref, out, entry_stop_loss_reset=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_loss_reset=True)
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "47"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
