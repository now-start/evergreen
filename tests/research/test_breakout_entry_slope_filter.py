from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from test_breakout_entry_window import entry_path, simulate

from evergreen.market import Candle, write_json
from evergreen.research.experiments.breakout_meta import costs


@pytest.mark.parametrize("oldest,buys", [("99.99", True), ("100", False), ("100.01", False)])
def test_prior_slope_strict_boundary(oldest: str, buys: bool) -> None:
    bars = entry_path()
    bars[7] = bars[7].model_copy(update={"close": D(oldest), "low": D(99), "high": D(101)})
    assert bool(simulate(bars, breakout_entry_slope_filter=True).fills) is buys
    assert simulate(bars).fills


def test_current_and_preceding_excluded_and_future_cannot_move_entry() -> None:
    bars = entry_path()
    bars[6] = bars[6].model_copy(update={"close": D(9000), "high": D(9000)})
    bars[199] = bars[199].model_copy(update={"close": D(110)})
    assert not simulate(bars, breakout_entry_slope_filter=True).fills
    bars[7] = bars[7].model_copy(update={"close": D(99), "low": D(99)})
    bars[199] = bars[199].model_copy(update={"close": D(102)})
    before = simulate(bars, breakout_entry_slope_filter=True)
    assert before.fills
    bars[205] = bars[205].model_copy(update={"high": D(9000)})
    assert simulate(bars, breakout_entry_slope_filter=True).fills[0] == before.fills[0]


@pytest.mark.parametrize("delay", [0, 1])
def test_slope_only_on_entry_and_original_signal_time(delay: int) -> None:
    bars = entry_path()
    bars[7] = bars[7].model_copy(update={"close": D(99), "low": D(99)})
    base = simulate(bars, delay=delay)
    result = simulate(bars, delay=delay, breakout_entry_slope_filter=True)
    assert result == base and result.fills
    assert result.fills[0].time == bars[200 + delay].open_time
    assert result.fills[0].signal_time == bars[199].close_time


def test_invalid_combinations_and_required_initial_history() -> None:
    with pytest.raises(ValueError, match="방향"):
        simulate(entry_path(), 168, breakout_entry_slope_filter=True)
    with pytest.raises(ValueError, match="방향"):
        simulate(entry_path(), breakout_entry_slope_filter=True, breakout_entry_trend_filter=True)
    from evergreen.research.experiments.strategy_family import simulate as prior

    bars = entry_path()
    with pytest.raises(ValueError, match="193"):
        prior(
            bars,
            bars[192].open_time,
            "liquidation-buffer",
            costs(),
            0,
            entry_lookback=84,
            entry_slope_filter=True,
        )
    prior(
        bars,
        bars[193].open_time,
        "liquidation-buffer",
        costs(),
        0,
        entry_lookback=84,
        entry_slope_filter=True,
    )


def test_falling_mean_during_position_does_not_add_exit() -> None:
    bars = entry_path()
    bars[7] = bars[7].model_copy(update={"close": D(80), "low": D(80)})
    bars[9] = bars[9].model_copy(update={"close": D(110), "high": D(110)})
    bars[199] = bars[199].model_copy(update={"high": D(103)})
    bars[201] = bars[201].model_copy(update={"close": D(99), "low": D(98)})
    base = simulate(bars)
    result = simulate(bars, breakout_entry_slope_filter=True)
    assert base.fills and result == base
    assert sum(b.close for b in bars[33:201]) < sum(b.close for b in bars[9:177])
    assert not any(f.side == "sell" and f.time == bars[202].open_time for f in result.fills)


@pytest.mark.parametrize("corrupt", [False, True])
def test_study_replays_six_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from evergreen.research.experiments import breakout_entry_window as runner
    from evergreen.research.experiments.regime import SCENARIOS
    from evergreen.research.experiments.strategy_family import simulate as prior

    bars = entry_path()
    start = bars[200].open_time

    def blocks(raw: Path, output: Path) -> list[list[Candle]]:
        output.mkdir()
        write_json(output / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(runner, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    group = ref / "seed-17/continuous/block-000"
    group.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "65"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in (*runner.MODELS, runner.TREND_CANDIDATE):
            short = name in (runner.CANDIDATE, runner.TREND_CANDIDATE)
            data = prior(
                bars,
                start,
                "liquidation-buffer" if short else name,
                costs(fee, slip),
                delay,
                entry_lookback=84 if short else 168,
                entry_trend_filter=name == runner.TREND_CANDIDATE,
            ).model_dump(mode="json")
            if corrupt and name == runner.TREND_CANDIDATE:
                data["total_fees"] = "123"
            write_json(group / f"{scenario}.{name}.json", data)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="대조"):
            runner.run_study(tmp_path, ref, out, slope_filter=True)
        assert not (out / "status.json").exists() and (out / "failure.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, slope_filter=True)
        status = json.loads((out / "status.json").read_text())
        protocol = json.loads((out / "protocol.json").read_text())
        assert status["control_comparisons"] == 24 and status["pnl_checks"] == 28
        assert not status["live_enabled"]
        assert protocol["control_entry_lookbacks"][runner.TREND_CANDIDATE] == 84
        assert protocol["entry_trend_filter"] is None
