from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from test_breakout_liquidation_buffer import path

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Result
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.strategy_family import CONTROLS, simulate


def replay(bars: list[Candle], cadence: int = 4, delay: int = 0, **kwargs: Any) -> Result:
    return simulate(
        bars,
        bars[200].open_time,
        "liquidation-buffer",
        costs(),
        delay,
        entry_cadence_hours=cadence,
        **kwargs,
    )


@pytest.mark.parametrize(
    "shift,allowed", [(0, True), (1, False), (2, False), (3, False), (16, True)]
)
def test_initial_signal_uses_utc_close_boundary(shift: int, allowed: bool) -> None:
    bars = [
        b.model_copy(update={"open_time": b.open_time + timedelta(hours=shift)}) for b in path()
    ]
    base = replay(bars, 1)
    result = replay(bars)
    assert base.fills[0].time == bars[200].open_time
    assert (
        any(f.side == "buy" and f.signal_time == bars[199].close_time for f in result.fills)
        is allowed
    )
    assert all(
        f.signal_time is not None and f.signal_time.hour % 4 == 0
        for f in result.fills
        if f.side == "buy"
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_pending_fill_and_hourly_exit_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    from evergreen.research import backtest

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    # Sell signal at 13 UTC, outside the entry cadence, must still execute.
    bars[203] = bars[203].model_copy(update={"close": D(108), "low": D(103), "high": D(109)})
    bars[204] = bars[204].model_copy(update={"close": D(99), "low": D(98)})
    base, result = replay(bars, 1, delay), replay(bars, 4, delay)
    assert base == result
    assert result.fills[0].time == bars[200 + delay].open_time
    assert result.fills[0].signal_time == bars[199].close_time
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].signal_time.hour % 4 != 0
    assert result.fills[1].time == bars[205 + delay].open_time


def test_skipped_signal_is_not_queued_for_next_allowed_hour() -> None:
    bars = [b.model_copy(update={"open_time": b.open_time + timedelta(hours=1)}) for b in path()]
    # Initial 09 UTC breakout disappears before 12 UTC.
    for i in range(200, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(100), "close": D(100), "low": D(99), "high": D(101)}
        )
    assert replay(bars, 1).fills
    assert not replay(bars).fills


def test_default_parity_and_invalid_combinations() -> None:
    bars = path()
    assert replay(bars, 1) == simulate(bars, bars[200].open_time, "liquidation-buffer", costs(), 0)
    for cadence in (0, 2, 6):
        with pytest.raises(ValueError, match="간격"):
            replay(bars, cadence)
    with pytest.raises(ValueError, match="간격"):
        replay(bars, entry_lookback=84)
    with pytest.raises(ValueError, match="후보"):
        simulate(bars, bars[200].open_time, "breakout-v1", costs(), 0, entry_cadence_hours=4)


def test_next_allowed_entry_uses_new_signal_and_current_history() -> None:
    bars = [b.model_copy(update={"open_time": b.open_time + timedelta(hours=1)}) for b in path()]
    bars[202] = bars[202].model_copy(update={"close": D(110), "high": D(111)})
    result = replay(bars)
    assert result.fills[0].side == "buy"
    assert result.fills[0].signal_time == bars[202].close_time
    assert result.fills[0].signal_time.hour == 12
    assert result.fills[0].time == bars[203].open_time


def test_channel_reset_on_nonentry_hour_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.research import backtest

    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: D(2))
    bars = path()
    bars[204] = bars[204].model_copy(update={"open": D(110), "high": D(111)})
    bars[249] = bars[249].model_copy(update={"close": D(95), "low": D(94)})
    bars[250] = bars[250].model_copy(
        update={"open": D(100), "close": D(100), "low": D(99), "high": D(101)}
    )
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(115), "close": D(115), "high": D(116), "low": D(114)}
        )
    assert bars[249].close_time.hour % 4 != 0
    result = replay(bars)
    assert len(result.fills) == 4
    assert result.fills[2].signal_time == bars[251].close_time
    assert result.fills[2].time == bars[252].open_time


@pytest.mark.parametrize("corrupt", [False, True])
def test_study_replays_four_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from evergreen.research.experiments import breakout_entry_window as runner
    from evergreen.research.experiments.regime import SCENARIOS

    bars = path()
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
    write_json(ref / "protocol.json", {"experiment": "66"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in CONTROLS:
            data = simulate(bars, start, name, costs(fee, slip), delay).model_dump(mode="json")
            if corrupt and name == "liquidation-buffer":
                data["total_fees"] = "123"
            write_json(group / f"{scenario}.{name}.json", data)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="대조"):
            runner.run_study(tmp_path, ref, out, entry_cadence=True)
        assert not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_cadence=True)
        status = json.loads((out / "status.json").read_text())
        protocol = json.loads((out / "protocol.json").read_text())
        assert status["control_comparisons"] == 16 and status["pnl_checks"] == 20
        assert not status["live_enabled"]
        assert protocol["entry_lookback"] == 168
        assert protocol["entry_cadence_hours"] == 4
        assert protocol["control_entry_cadence_hours"] == dict.fromkeys(CONTROLS, 1)
        assert protocol["entry_trend_filter"] is None and protocol["entry_slope_filter"] is None
    with pytest.raises(ValueError):
        runner.run_study(tmp_path, ref, tmp_path / "invalid", entry_cadence=True, slope_filter=True)
