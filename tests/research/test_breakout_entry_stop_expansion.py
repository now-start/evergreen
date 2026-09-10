from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_breakout_entry_stop import setup

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Costs, Result, run_backtest
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.strategies import Strategy


def expanding() -> list[Candle]:
    bars = setup()
    for i in range(175, 199):
        bars[i] = bars[i].model_copy(update={"low": Decimal(98)})
    assert prior_mean_true_range(bars[:200]) > prior_mean_true_range(bars[:200], lookback=168)
    return bars


def simulate(
    bars: list[Candle],
    *,
    enabled: bool = True,
    delay: int = 0,
    capital: Decimal | int = 1000000,
    **kwargs: Any,
) -> Result:
    return run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_expansion=enabled,
        extra_delay_bars=delay,
        **kwargs,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_expanding_signal_enables_fixed_stop_with_delayed_entry_and_strict_close(
    delay: int,
) -> None:
    bars = expanding()
    if delay:
        bars[201] = bars[201].model_copy(update={"open": Decimal(108), "high": Decimal(109)})
    threshold = bars[200 + delay].open - 3 * prior_mean_true_range(bars[:200])
    bars[203] = bars[203].model_copy(update={"close": threshold, "low": Decimal(1)})
    bars[204] = bars[204].model_copy(
        update={"close": threshold - Decimal(".01"), "low": Decimal(1)}
    )
    result = simulate(bars, delay=delay)
    assert result == simulate(bars, enabled=False, delay=delay)
    assert result.fills[1].time == bars[205 + delay].open_time
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].reason == "signal"


@pytest.mark.parametrize("relation", ["less", "equal", "zero"])
def test_nonexpanding_entry_is_kept_but_extra_stop_stays_disabled(relation: str) -> None:
    bars = setup()
    if relation != "less":
        for i in range(199):
            bars[i] = bars[i].model_copy(update={"low": Decimal(99)})
            if relation == "zero":
                bars[i] = bars[i].model_copy(
                    update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100))
                )
    short, slow = prior_mean_true_range(bars[:200]), prior_mean_true_range(bars[:200], lookback=168)
    assert short < slow if relation == "less" else short == slow
    bars[203] = bars[203].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    # Signal bar's wide low makes later TR expand; it must not activate this position.
    assert prior_mean_true_range(bars[:204]) > prior_mean_true_range(bars[:204], lookback=168)
    actual = simulate(bars)
    original = run_backtest(bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    assert actual == original and actual.fills[0].time == bars[200].open_time
    assert actual.fills[1].reason == "settlement"


def test_active_stop_does_not_turn_off_when_later_tr_contracts() -> None:
    bars = expanding()
    assert prior_mean_true_range(bars[:241]) < prior_mean_true_range(bars[:241], lookback=168)
    bars[240] = bars[240].model_copy(update={"close": Decimal("94.99"), "low": Decimal(1)})
    result = simulate(bars)
    assert result.fills[1].time == bars[241].open_time and result.fills[1].reason == "signal"


def test_next_buy_can_disable_previous_active_stop() -> None:
    bars = expanding()
    bars[204] = bars[204].model_copy(update={"close": Decimal(94), "low": Decimal(1)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    bars[251] = bars[251].model_copy(
        update={
            "open": Decimal(112),
            "close": Decimal(112),
            "high": Decimal(113),
            "low": Decimal(111),
        }
    )
    assert prior_mean_true_range(bars[:251]) < prior_mean_true_range(bars[:251], lookback=168)
    bars[252] = bars[252].model_copy(update={"close": Decimal(104), "low": Decimal(1)})
    result = simulate(bars)
    assert [f.time for f in result.fills[:3]] == [bars[i].open_time for i in (200, 205, 251)]
    assert result.fills[-1].reason == "settlement"
    assert simulate(bars, enabled=False).fills[-1].time == bars[253].open_time


def test_next_buy_can_enable_previous_inactive_stop_and_risk_still_wins() -> None:
    bars = setup()
    bars[160] = bars[160].model_copy(update={"low": Decimal(98)})
    bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    assert prior_mean_true_range(bars[:200]) < prior_mean_true_range(bars[:200], lookback=168)
    bars[203] = bars[203].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    for i in range(226, 250):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(104),
                "close": Decimal(104),
                "high": Decimal(104),
                "low": Decimal(101),
            }
        )
    bars[250] = bars[250].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    bars[251] = bars[251].model_copy(
        update={
            "open": Decimal(112),
            "close": Decimal(112),
            "high": Decimal(113),
            "low": Decimal(111),
        }
    )
    assert prior_mean_true_range(bars[:251]) > prior_mean_true_range(bars[:251], lookback=168)
    bars[252] = bars[252].model_copy(update={"close": Decimal("102.5"), "low": Decimal(102)})
    result = simulate(bars)
    assert [f.time for f in result.fills] == [bars[i].open_time for i in (200, 204, 251, 253)]
    bars[203] = bars[203].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    risk = simulate(bars)
    assert risk.halted and len(risk.fills) == 2 and risk.fills[1].reason == "risk"
    unfilled = simulate(setup(), capital=1000)
    assert not unfilled.fills and unfilled.rejections


def test_expansion_requires_entry_stop_and_rejects_floor_and_short_history(tmp_path: Path) -> None:
    bars = setup()
    args: tuple[list[Candle], datetime, Decimal, Costs, Strategy] = (
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
    )
    with pytest.raises(ValueError, match="확대"):
        run_backtest(*args, breakout_entry_stop_expansion=True)
    with pytest.raises(ValueError, match="확대"):
        simulate(bars, breakout_entry_stop_floor=True)
    with pytest.raises(ValueError, match="170 hours of warmup"):
        run_backtest(
            bars,
            bars[169].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop=True,
            breakout_entry_stop_expansion=True,
        )
    with pytest.raises(ValueError, match="확대"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_expansion_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path, tmp_path, tmp_path / "out", entry_stop_floor=True, entry_stop_expansion=True
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_expansion_pipeline_preserves_floor_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    bars = setup()
    bars[203] = bars[203].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
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
    write_json(ref / "protocol.json", {"experiment": "41"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-tr-floor"):
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
                breakout_entry_stop_floor=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_expansion=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_expansion=True)
        block = out / "seed-17/continuous/block-000"
        result = json.loads((block / "base.entry-stop-expansion.json").read_text())
        assert result["fills"][0]["time"] == bars[200].open_time.isoformat().replace("+00:00", "Z")
        assert result["fills"][1]["reason"] == "settlement"
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "42"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
