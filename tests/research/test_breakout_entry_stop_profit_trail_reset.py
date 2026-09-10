from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from test_breakout_entry_stop import setup

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Result, run_backtest
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(
    bars: list[Candle], *, enabled: bool = True, delay: int = 0, capital: Decimal | int = 1000000
) -> Result:
    return run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_confirmations=2,
        breakout_entry_stop_channel_reset=True,
        breakout_entry_stop_profit_trail=enabled,
        extra_delay_bars=delay,
    )


def profit_path() -> list[Candle]:
    bars = setup()
    bars[202] = bars[202].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_exact_activation_two_closes_and_pending_rebound(delay: int) -> None:
    bars = profit_path()
    result = simulate(bars, delay=delay)
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].time == bars[205 + delay].open_time
    assert result.fills[1].reason == "signal"
    assert simulate(bars, enabled=False, delay=delay).fills[1].time > result.fills[1].time
    bars[202] = bars[202].model_copy(update={"close": Decimal("109.99"), "high": Decimal(500)})
    assert simulate(bars, delay=delay) == simulate(bars, enabled=False, delay=delay)


@pytest.mark.parametrize("reset", [Decimal(104), Decimal("104.01")])
def test_equality_or_recovery_resets_breaches_without_lowering_peak(reset: Decimal) -> None:
    bars = profit_path()
    bars[204] = bars[204].model_copy(update={"close": reset})
    for i in (205, 206):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    result = simulate(bars)
    assert result.fills[1].signal_time == bars[206].close_time
    assert result.fills[1].time == bars[207].open_time


def test_delayed_entry_excludes_prebuy_peak_and_uses_original_signal_tr() -> None:
    bars = setup()
    bars[200] = bars[200].model_copy(update={"close": Decimal(500), "high": Decimal(501)})
    bars[201] = bars[201].model_copy(
        update={
            "open": Decimal(108),
            "high": Decimal(109),
            "close": Decimal(101),
            "low": Decimal(100),
        }
    )
    bars[202] = bars[202].model_copy(update={"close": Decimal(101), "low": Decimal(100)})
    # Fixed threshold 108 - 3*2 = 102; prebuy 500 cannot activate a trailing threshold.
    result = simulate(bars, delay=1)
    assert result.fills[1].signal_time == bars[202].close_time
    assert result.fills[1].time == bars[204].open_time
    assert result == simulate(bars, enabled=False, delay=1)


@pytest.mark.parametrize("risk", [False, True])
def test_channel_or_risk_bypasses_confirmation(risk: bool) -> None:
    bars = setup()
    bars[160] = bars[160].model_copy(update={"low": Decimal(98)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    bars[203] = bars[203].model_copy(
        update={"close": Decimal(70 if risk else 97), "low": Decimal(69 if risk else 96)}
    )
    result = simulate(bars)
    assert result.fills[1].time == bars[204].open_time
    assert result.fills[1].reason == ("risk" if risk else "signal")
    assert result.halted == risk


def test_extra_trailing_sell_locks_and_new_buy_resets_peak() -> None:
    bars = profit_path()
    bars[210] = bars[210].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    locked = simulate(bars)
    assert len(locked.fills) == 2
    bars[249] = bars[249].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    # A new entry below the previous peak must not inherit its trailing stop.
    for i in (251, 252):
        bars[i] = bars[i].model_copy(update={"open": Decimal(103), "close": Decimal(103)})
    result = simulate(bars)
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[-1].reason == "settlement"
    assert not simulate(bars, capital=1000).fills


def test_rejected_sell_preserves_peak_for_retry() -> None:
    bars = profit_path()
    bars[205] = bars[205].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    for i in (208, 209):
        bars[i] = bars[i].model_copy(update={"close": Decimal(103), "low": Decimal(102)})
    result = simulate(bars, capital=5100)
    assert result.rejections[0].side == "sell"
    assert result.rejections[0].time == bars[205].open_time
    assert result.fills[1].time == bars[210].open_time


def test_zero_tr_never_activates_and_new_high_resets_breaches() -> None:
    bars = profit_path()
    bars[204] = bars[204].model_copy(update={"close": Decimal(111), "high": Decimal(112)})
    for i in (205, 206):
        bars[i] = bars[i].model_copy(update={"close": Decimal(104)})
    assert simulate(bars).fills[1].signal_time == bars[206].close_time
    flat = setup()
    for i in range(174, 199):
        flat[i] = flat[i].model_copy(
            update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100))
        )
    flat[202] = flat[202].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    assert simulate(flat) == simulate(flat, enabled=False)


def test_profit_trail_requires_channel_reset_and_declared_models(tmp_path: Path) -> None:
    bars = setup()
    with pytest.raises(ValueError, match="상승 후 추적"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop_profit_trail=True,
        )
    with pytest.raises(ValueError, match="상승 후 추적"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_profit_trail_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_channel_reset=True,
            entry_stop_profit_trail_reset=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_channel_reset_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    bars = profit_path()
    start = bars[200].open_time

    def blocks(raw: Path, out: Path) -> list[list[Candle]]:
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(runner, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    folder = ref / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "44"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-channel-reset"):
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
                breakout_entry_stop_channel_reset=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_profit_trail_reset=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_profit_trail_reset=True)
        result = json.loads(
            (
                out / "seed-17/continuous/block-000/base.entry-stop-profit-trail-reset.json"
            ).read_text()
        )
        assert result["fills"][1]["signal_time"] == bars[204].close_time.isoformat().replace(
            "+00:00", "Z"
        )
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "45"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
