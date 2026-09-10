import json
from datetime import timedelta
from decimal import Decimal

import pytest
from test_breakout_entry_stop import setup

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(bars, *, enabled=True, delay=0, capital=1000000, **kwargs):
    return run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_confirmations=2,
        breakout_entry_stop_channel_reset=enabled,
        extra_delay_bars=delay,
        **kwargs,
    )


def extra_stop():
    bars = setup()
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    return bars


def test_extra_stop_locks_until_strict_closed_channel_reset_then_new_breakout():
    bars = extra_stop()
    last = bars[-1]
    bars.extend(
        last.model_copy(update={"open_time": last.open_time + timedelta(hours=i)})
        for i in range(1, 21)
    )
    bars[210] = bars[210].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    # Equal close and a lower intrabar low must not release the latch.
    bars[260] = bars[260].model_copy(update={"close": Decimal(101), "low": Decimal(100)})
    bars[261] = bars[261].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    bars[262] = bars[262].model_copy(update={"close": Decimal(99), "low": Decimal(98)})
    bars[263] = bars[263].model_copy(update={"close": Decimal(114), "high": Decimal(115)})
    bars[264] = bars[264].model_copy(
        update={
            "open": Decimal(114),
            "close": Decimal(114),
            "high": Decimal(115),
            "low": Decimal(113),
        }
    )
    locked = simulate(bars)
    assert [f.time for f in locked.fills[:3]] == [bars[i].open_time for i in (200, 205, 264)]
    assert simulate(bars, enabled=False).fills[2].time == bars[211].open_time
    assert locked.fills[2].signal_time == bars[263].close_time


def test_no_reset_keeps_cash_even_after_multiple_breakouts():
    bars = extra_stop()
    for i, price in ((210, 110), (230, 112), (250, 114)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(price), "high": Decimal(price + 1)})
    locked = simulate(bars)
    assert len(locked.fills) == 2 and locked.btc == 0 and not locked.halted
    assert simulate(bars, enabled=False).fills[2].time == bars[211].open_time


def test_actual_sell_bar_can_release_but_pre_sell_delay_bar_cannot():
    bars = extra_stop()
    bars[160] = bars[160].model_copy(update={"low": Decimal(96)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(98)})
    bars[205] = bars[205].model_copy(update={"close": Decimal(95), "low": Decimal(94)})
    bars[208] = bars[208].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    assert simulate(bars).fills[2].time == bars[209].open_time
    delayed = simulate(bars, delay=1)
    assert delayed.fills[1].time == bars[206].open_time
    assert len(delayed.fills) == 2
    bars[255] = bars[255].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[256] = bars[256].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    assert simulate(bars, delay=1).fills[2].time == bars[258].open_time


@pytest.mark.parametrize("delay", [0, 1])
def test_simultaneous_channel_and_stop_is_channel_exit_without_lock(delay):
    bars = extra_stop()
    bars[160] = bars[160].model_copy(update={"low": Decimal(97)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(98)})
    bars[203] = bars[203].model_copy(update={"low": Decimal(97)})
    bars[204] = bars[204].model_copy(update={"close": Decimal(96), "low": Decimal(95)})
    bars[208] = bars[208].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    actual = simulate(bars, delay=delay)
    assert actual == simulate(bars, enabled=False, delay=delay)
    assert actual.fills[1].signal_time == bars[204].close_time
    assert actual.fills[2].time == bars[209 + delay].open_time


def test_risk_replaces_pending_extra_stop_without_resetting_halt():
    bars = extra_stop()
    bars[205] = bars[205].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.halted and len(result.fills) == 2
    assert result.fills[1].reason == "risk" and result.fills[1].time == bars[207].open_time
    unfilled = simulate(setup(), capital=1000)
    assert not unfilled.fills and unfilled.rejections


def test_failed_sell_does_not_discard_position_or_block_retry():
    bars = extra_stop()
    bars[205] = bars[205].model_copy(update={"open": Decimal(97), "low": Decimal(96)})
    result = simulate(bars, capital=5100)
    assert result.rejections[0].time == bars[205].open_time
    assert result.rejections[0].side == "sell"
    # Rebound cancels the signal, not an already queued order; later two low closes retry.
    for i in (208, 209):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    result = simulate(bars, capital=5100)
    assert result.fills[1].time == bars[210].open_time


def test_reset_requires_confirmed_stop_and_binds_only_declared_models(tmp_path):
    bars = setup()
    args = (bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    for flags in ({}, {"breakout_entry_stop": True}, {"breakout_entry_stop_confirmations": 2}):
        with pytest.raises(ValueError, match="채널 리셋"):
            run_backtest(*args, breakout_entry_stop_channel_reset=True, **flags)
    with pytest.raises(ValueError, match="채널 리셋"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_channel_reset_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            confirmed_entry_stop=True,
            entry_stop_channel_reset=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_reset_pipeline_preserves_confirm2_control(tmp_path, monkeypatch, corrupt):
    bars = extra_stop()
    bars[210] = bars[210].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
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
    write_json(ref / "protocol.json", {"experiment": "43"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-confirm2"):
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
                breakout_entry_stop_confirmations=2 if filtered else 1,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_channel_reset=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_channel_reset=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.entry-stop-channel-reset.json").read_text()
        )
        assert len(result["fills"]) == 2
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "44"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
