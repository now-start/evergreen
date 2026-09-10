from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from test_breakout_exit import fixture

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Costs, run_backtest
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.strategies import Strategy


def trailing_bars() -> list[Candle]:
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[174] = bars[174].model_copy(update={"close": Decimal(90), "low": Decimal(89)})
    # Prior24 TR=(11+23*2)/24=2.375. Signal range must not enter this estimate.
    bars[199] = bars[199].model_copy(update={"high": Decimal(200), "low": Decimal(1)})
    bars[200] = bars[200].model_copy(update={"high": Decimal(300)})
    bars[210] = bars[210].model_copy(update={"high": Decimal(111), "close": Decimal(110)})
    bars[211] = bars[211].model_copy(update={"low": Decimal(102), "close": Decimal("102.875")})
    bars[212] = bars[212].model_copy(update={"low": Decimal(102), "close": Decimal("102.8")})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_trailing_uses_signal_range_post_fill_closes_and_strict_boundary(delay: int) -> None:
    bars = trailing_bars()
    if delay:
        # Before delayed fill at201: neither close120 nor high300 is a held peak.
        bars[200] = bars[200].model_copy(update={"close": Decimal(120)})
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    original = run_backtest(*args, extra_delay_bars=delay)
    assert original == run_backtest(*args, extra_delay_bars=delay, breakout_trailing_exit=False)
    result = run_backtest(*args, extra_delay_bars=delay, breakout_trailing_exit=True)
    assert result.fills[0] == original.fills[0]
    assert result.fills[1].time == bars[213 + delay].open_time
    assert result.fills[1].reference_price == bars[213 + delay].open
    assert result.fills[1].reason == "signal"
    # Only future data changes: first buy/sell pair must be immutable.
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(10000)})
    assert (
        run_backtest(*args, extra_delay_bars=delay, breakout_trailing_exit=True).fills[:2]
        == result.fills[:2]
    )


@pytest.mark.parametrize("zero_range", [False, True])
def test_trailing_peak_and_frozen_range_reset_on_next_actual_buy(zero_range: bool) -> None:
    bars = trailing_bars()
    bars[213] = bars[213].model_copy(
        update={"open": Decimal(110), "close": Decimal(110), "high": Decimal(111)}
    )
    bars += [
        bars[-1].model_copy(update={"open_time": bars[-1].open_time + timedelta(hours=i)})
        for i in range(1, 211)
    ]
    bars[380] = bars[380].model_copy(update={"low": Decimal(80)})
    if zero_range:
        for i in range(394, 419):
            bars[i] = bars[i].model_copy(
                update=dict.fromkeys(("open", "high", "low", "close"), Decimal(104))
            )
    bars[419] = bars[419].model_copy(update={"high": Decimal(107), "close": Decimal(106)})
    bars[420] = bars[420].model_copy(
        update={"open": Decimal(106), "high": Decimal(107), "close": Decimal(106)}
    )
    bars[422] = bars[422].model_copy(
        update={"low": Decimal(97), "close": Decimal(98) if zero_range else Decimal("101.5")}
    )
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_trailing_exit=True,
    )
    assert [(f.side, f.time) for f in result.fills[:3]] == [
        ("buy", bars[200].open_time),
        ("sell", bars[213].open_time),
        ("buy", bars[420].open_time),
    ]
    assert len(result.fills) == 4 and result.fills[3].reason == "settlement"
    assert not result.halted


@pytest.mark.parametrize("delay", [0, 1])
def test_trailing_retains_original_channel_exit_and_risk_priority(delay: int) -> None:
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(1)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    result = run_backtest(*args, breakout_trailing_exit=True, extra_delay_bars=delay)
    assert result.fills[1].time == bars[251 + delay].open_time
    assert result.fills[1].reason == "signal"
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = run_backtest(*args, breakout_trailing_exit=True, extra_delay_bars=delay)
    assert result.fills[1].time == bars[251 + delay].open_time
    assert result.fills[1].reason == "risk" and result.halted


def test_trailing_rejected_buys_remain_flat_and_zero_range_keeps_original() -> None:
    bars = fixture()
    for i in range(199):
        bars[i] = bars[i].model_copy(
            update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100))
        )
    bars[250] = bars[250].model_copy(update={"close": Decimal(99), "low": Decimal(98)})
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    assert run_backtest(*args) == run_backtest(*args, breakout_trailing_exit=True)
    small = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000),
        costs(),
        "breakout-v1",
        breakout_trailing_exit=True,
    )
    assert small.rejections and not small.fills and small.cash == 1000


@pytest.mark.parametrize(
    "strategy,extra",
    [
        ("cash", {}),
        ("breakout-v1", {"breakout_failure_exit": True}),
        ("breakout-v1", {"breakout_exit_lookback": 24}),
        ("breakout-v1", {"breakout_cooldown_hours": 24}),
        ("breakout-v1", {"breakout_risk_budget": True}),
    ],
)
def test_trailing_rejects_unrelated_or_combined_modes(strategy: str, extra: Any) -> None:
    bars = fixture()
    with pytest.raises(ValueError, match="추적"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            cast(Strategy, strategy),
            breakout_trailing_exit=True,
            **extra,
        )


def test_trailing_dispatch_rejects_combined_modes_and_unknown_candidate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="동시"):
        breakout_confirmation.run_study(
            tmp_path, tmp_path, tmp_path / "out", trailing_exit=True, extension_cap=True
        )
    with pytest.raises(ValueError, match="추적"):
        evaluate(
            {"breakout-v1": [], "cash": [], "prior": [], "candidate": []},
            tmp_path,
            models=("candidate",),
            trailing_exit_models=("breakout-v1",),
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("profit_mode", [False, True])
def test_trailing_pipeline_preserves_cap_control_and_original_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool, profit_mode: bool
) -> None:
    bars = trailing_bars()
    # Initial entry rejected by cap (extension3 > TR2.375), but not by trailing candidate.
    bars[199] = bars[199].model_copy(update={"close": Decimal(104)})
    start = bars[200].open_time
    controls = ("breakout-v1", "trailing-tr24" if profit_mode else "entry-cap-tr24")
    candidate = "profit-trailing-tr24" if profit_mode else "trailing-tr24"
    options = {"profit_trailing_exit": True} if profit_mode else {"trailing_exit": True}

    def blocks(raw: Path, out: Path) -> list[list[Candle]]:
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(breakout_confirmation, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    folder = ref / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "34" if profit_mode else "33"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in controls:
            cap = name == "entry-cap-tr24"
            r = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if name != "breakout-v1" else "breakout-v1",
                extra_delay_bars=delay,
                regimes=breakout_confirmation.extension_schedule(bars, start)
                if cap
                else dict.fromkeys((b.close_time for b in bars if b.close_time >= start), 2)
                if name != "breakout-v1"
                else None,
                regime_policy="breakout-filter" if name != "breakout-v1" else "routing",
                breakout_trailing_exit=name == "trailing-tr24",
            ).model_dump(mode="json")
            if corrupt and name == controls[-1]:
                r["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", r)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            breakout_confirmation.run_study(tmp_path, ref, out, **options)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        breakout_confirmation.run_study(tmp_path, ref, out, **options)
        result = json.loads(
            (out / f"seed-17/continuous/block-000/base.{candidate}.json").read_text()
        )
        assert result["fills"][0]["time"] == bars[200].open_time.isoformat().replace("+00:00", "Z")
        if profit_mode:
            assert result["fills"][1]["reason"] == "settlement"
        else:
            assert result["fills"][1]["time"] == bars[213].open_time.isoformat().replace(
                "+00:00", "Z"
            )
        assert json.loads((out / "protocol.json").read_text())["experiment"] == (
            "35" if profit_mode else "34"
        )
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
        assert len(list((out / "seed-17/continuous/block-000").glob("*.json"))) == 21
