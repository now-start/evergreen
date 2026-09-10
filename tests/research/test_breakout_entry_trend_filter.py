from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from test_breakout_entry_window import entry_path, simulate

from evergreen.market import Candle, write_json
from evergreen.research.experiments.breakout_meta import costs


def boundary_path() -> list[Candle]:
    bars = entry_path()
    for i in range(31, 115):
        bars[i] = bars[i].model_copy(
            update={"open": D(104), "close": D(104), "high": D(200), "low": D(100)}
        )
    return bars


@pytest.mark.parametrize("price,buys", [("102", False), ("102.00001", True), ("101.99999", False)])
def test_prior_mean_strict_boundary_and_current_exclusion(price: str, buys: bool) -> None:
    bars = boundary_path()
    value = D(price)
    bars[199] = bars[199].model_copy(update={"close": value, "low": min(value, D(102))})
    assert bool(simulate(bars, breakout_entry_trend_filter=True).fills) is buys
    assert simulate(bars).fills


def test_mean_oldest_included_and_preceding_excluded() -> None:
    bars = boundary_path()
    bars[30] = bars[30].model_copy(update={"open": D(5000), "close": D(5000), "high": D(5000)})
    bars[31] = bars[31].model_copy(update={"close": D("103.99")})
    assert simulate(bars, breakout_entry_trend_filter=True).fills
    bars[31] = bars[31].model_copy(update={"close": D("104.01")})
    assert not simulate(bars, breakout_entry_trend_filter=True).fills


@pytest.mark.parametrize("delay", [0, 1])
def test_approved_entry_keeps_exit_and_signal_time(delay: int) -> None:
    bars = entry_path()
    base = simulate(bars, delay=delay)
    changed = simulate(bars, delay=delay, breakout_entry_trend_filter=True)
    assert base.fills and changed == base
    assert changed.fills[0].signal_time == bars[199].close_time
    assert changed.fills[0].time == bars[200 + delay].open_time
    bars[205] = bars[205].model_copy(update={"high": D(9000)})
    assert (
        simulate(bars, delay=delay, breakout_entry_trend_filter=True).fills[0] == changed.fills[0]
    )


def test_filter_requires_short_window() -> None:
    with pytest.raises(ValueError, match="평균가격"):
        simulate(entry_path(), 168, breakout_entry_trend_filter=True)


def test_holding_below_mean_does_not_add_an_exit() -> None:
    bars = entry_path()
    bars[199] = bars[199].model_copy(update={"high": D(103)})
    bars[201] = bars[201].model_copy(update={"close": D(99), "low": D(98)})
    base = simulate(bars)
    changed = simulate(bars, breakout_entry_trend_filter=True)
    assert base.fills and changed == base
    assert bars[201].close * 168 < sum(b.close for b in bars[33:201])
    assert not any(f.side == "sell" and f.time == bars[202].open_time for f in changed.fills)


@pytest.mark.parametrize("corrupt", [False, True])
def test_trend_study_matches_all_five_frozen_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from evergreen.research.experiments import breakout_entry_window as runner
    from evergreen.research.experiments.regime import SCENARIOS
    from evergreen.research.experiments.strategy_family import simulate as previous

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
    write_json(ref / "protocol.json", {"experiment": "64"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in runner.MODELS:
            data = previous(
                bars,
                start,
                "liquidation-buffer" if name == runner.CANDIDATE else name,
                costs(fee, slip),
                delay,
                entry_lookback=84 if name == runner.CANDIDATE else 168,
            ).model_dump(mode="json")
            if corrupt and name == runner.CANDIDATE:
                data["total_fees"] = "123"
            write_json(group / f"{scenario}.{name}.json", data)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="대조"):
            runner.run_study(tmp_path, ref, out, trend_filter=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, trend_filter=True)
        status = json.loads((out / "status.json").read_text())
        protocol = json.loads((out / "protocol.json").read_text())
        assert protocol["control_entry_lookbacks"][runner.CANDIDATE] == 84
        assert protocol["control_entry_lookbacks"]["breakout-v1"] == 168
        assert status["control_comparisons"] == 20 and status["pnl_checks"] == 24
        assert not status["live_enabled"]
        data = json.loads(
            (out / f"seed-17/continuous/block-000/base.{runner.TREND_CANDIDATE}.json").read_text()
        )
        assert data == simulate(bars, breakout_entry_trend_filter=True).model_dump(mode="json")
