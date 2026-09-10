from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from test_breakout_entry_stop_trend_context import MODES
from test_strategies import from_closes

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Result, run_backtest
from evergreen.research.experiments.breakout_meta import costs


def entry_path() -> list[Candle]:
    bars = from_closes([D(100)] * 199 + [D(102)] + [D(104)] * 10)
    bars[180] = bars[180].model_copy(update={"low": D(99)})
    bars[50] = bars[50].model_copy(update={"high": D(200)})
    bars[199] = bars[199].model_copy(update={"high": D(1000)})
    return bars


def simulate(
    bars: list[Candle], lookback: int = 84, delay: int = 0, regime: int = 2, **kwargs: Any
) -> Result:
    return run_backtest(
        bars,
        bars[200].open_time,
        D(1000000),
        costs(),
        "regime-mlp-v1",
        regimes={b.close_time: regime for b in bars[199:]},
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
        breakout_liquidation_buffer=True,
        breakout_entry_lookback=lookback,
        **MODES,
        **kwargs,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_short_window_excludes_current_bar_and_executes_next_open(delay: int) -> None:
    bars = entry_path()
    short = simulate(bars, delay=delay)
    assert short.fills[0].signal_time == bars[199].close_time
    assert short.fills[0].time == bars[200 + delay].open_time
    assert not simulate(bars, 168, delay).fills
    bars[205] = bars[205].model_copy(update={"high": D(10000)})
    assert simulate(bars, delay=delay).fills[0] == short.fills[0]


def test_oldest_included_bar_and_strict_threshold() -> None:
    bars = entry_path()
    bars[114] = bars[114].model_copy(update={"high": D(5000)})
    assert simulate(bars).fills
    bars[115] = bars[115].model_copy(update={"high": D(103)})
    assert not simulate(bars).fills
    bars = entry_path()
    bars[199] = bars[199].model_copy(update={"close": D("100.1")})
    assert not simulate(bars).fills


@pytest.mark.parametrize("regime", [-1, 0, 1])
def test_short_entry_still_obeys_regime_permission(regime: int) -> None:
    assert not simulate(entry_path(), regime=regime).fills


@pytest.mark.parametrize(
    "kwargs",
    [
        {"breakout_entry_lookback": 85},
        {"breakout_entry_lookback": 84},
    ],
)
def test_requires_frozen_buffer_policy(kwargs: Any) -> None:
    bars = entry_path()
    with pytest.raises(ValueError, match="진입 창"):
        run_backtest(bars, bars[200].open_time, D(1000000), costs(), "breakout-v1", **kwargs)


def test_default_window_is_unchanged_and_no_context_mix() -> None:
    from evergreen.research.experiments.strategy_family import simulate as previous

    bars = entry_path()
    assert simulate(bars, 168) == previous(
        bars, bars[200].open_time, "liquidation-buffer", costs(), 0
    )
    with pytest.raises(ValueError, match="진입 창"):
        simulate(bars, breakout_entry_context=True)


@pytest.mark.parametrize("corrupt", [False, True])
def test_study_reproduces_controls_and_rejects_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from evergreen.research.experiments import breakout_entry_window as runner
    from evergreen.research.experiments.regime import SCENARIOS
    from evergreen.research.experiments.strategy_family import CONTROLS
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
    write_json(ref / "protocol.json", {"experiment": "62"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in CONTROLS:
            data = previous(bars, start, name, costs(fee, slip), delay).model_dump(mode="json")
            if corrupt:
                data["total_fees"] = "123"
            write_json(group / f"{scenario}.{name}.json", data)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="대조"):
            runner.run_study(tmp_path, ref, out)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out)
        status = json.loads((out / "status.json").read_text())
        assert status["control_comparisons"] == 16 and status["pnl_checks"] == 20
        assert not status["live_enabled"]
        actual = json.loads(
            (out / f"seed-17/continuous/block-000/base.{runner.CANDIDATE}.json").read_text()
        )
        assert actual == simulate(bars).model_dump(mode="json")
        with pytest.raises(FileExistsError):
            runner.run_study(tmp_path, ref, out)
