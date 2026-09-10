from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_trend_confirmation import declining_path

from evergreen.market import Candle, write_json
from evergreen.research import backtest
from evergreen.research.backtest import Result
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


class ModeOptions(TypedDict):
    breakout_entry_stop: bool
    breakout_entry_stop_confirmations: int
    breakout_entry_stop_channel_reset: bool
    breakout_entry_stop_profit_trail: bool
    breakout_entry_stop_adaptive_trail: bool
    breakout_entry_stop_trend_confirmation: bool
    breakout_entry_stop_budget: bool


class OptionalModeOptions(TypedDict, total=False):
    breakout_entry_stop: bool
    breakout_entry_stop_confirmations: int
    breakout_entry_stop_channel_reset: bool
    breakout_entry_stop_profit_trail: bool
    breakout_entry_stop_adaptive_trail: bool
    breakout_entry_stop_trend_confirmation: bool
    breakout_entry_stop_budget: bool


MODES: ModeOptions = dict(
    breakout_entry_stop=True,
    breakout_entry_stop_confirmations=2,
    breakout_entry_stop_channel_reset=True,
    breakout_entry_stop_profit_trail=True,
    breakout_entry_stop_adaptive_trail=True,
    breakout_entry_stop_trend_confirmation=True,
    breakout_entry_stop_budget=True,
)


def simulate(
    bars: list[Candle], *, lookback: int = 168, delay: int = 0, start: datetime | None = None
) -> Result:
    return backtest.run_backtest(
        bars,
        start or bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        breakout_entry_stop_trend_lookback=lookback,
        **MODES,
    )


def long_decline() -> list[Candle]:
    bars = declining_path()
    for i in range(11, 179):
        bars[i] = bars[i].model_copy(update={"close": Decimal(102), "high": Decimal(102)})
    return bars


@pytest.mark.parametrize("delay", [0, 1])
def test_context_defers_short_lived_weakness_without_changing_entry(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    control, result = simulate(bars, lookback=24, delay=delay), simulate(bars, delay=delay)
    assert result.fills[0] == control.fills[0]
    assert control.fills[1].time == bars[205 + delay].open_time
    assert result.fills[1].time > control.fills[1].time


@pytest.mark.parametrize("delay", [0, 1])
def test_nonrising_long_context_keeps_exit_and_pending_rebound(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = long_decline()
    bars[205] = bars[205].model_copy(update={"close": Decimal(112), "high": Decimal(113)})
    result = simulate(bars, delay=delay)
    assert result.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].time == bars[205 + delay].open_time


def test_exact_mean_equality_window_edges_and_current_close_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = declining_path()
    for i in range(11, 179):
        bars[i] = bars[i].model_copy(update={"close": Decimal("100.875"), "high": Decimal(102)})
    assert sum(b.close for b in bars[179:203]) * 7 == sum(b.close for b in bars[11:179])
    # Equality qualifies 203, but a rising 204 breaks the consecutive count.
    assert simulate(bars).fills[1].signal_time != bars[204].close_time
    # Keep equality at 203; rolling the left boundary at 204 now yields nonrising.
    bars[11] = bars[11].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    bars[12] = bars[12].model_copy(update={"close": Decimal("131.75"), "high": Decimal(132)})
    assert sum(b.close for b in bars[179:203]) * 7 == sum(b.close for b in bars[11:179])
    assert sum(b.close for b in bars[181:205]) * 7 > sum(b.close for b in bars[13:181])
    result = simulate(bars)
    assert result.fills[1].signal_time == bars[204].close_time
    bars[203] = bars[203].model_copy(update={"high": Decimal(500)})
    assert simulate(bars).fills[:2] == result.fills[:2]


@pytest.mark.parametrize("kind", ["initial", "channel", "risk"])
def test_long_context_cannot_block_initial_channel_or_risk_exit(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = setup()
    if kind == "initial":
        for i in (203, 204):
            bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    elif kind == "channel":
        bars[160] = bars[160].model_copy(update={"low": Decimal(99)})
        bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
        bars[203] = bars[203].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    else:
        bars[203] = bars[203].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    signal = 204 if kind == "initial" else 203
    assert result.fills[1].signal_time == bars[signal].close_time
    assert result.fills[1].time == bars[signal + 2].open_time
    assert result.fills[1].reason == ("risk" if kind == "risk" else "signal")


def test_long_context_keeps_entry_budget_and_rejects_short_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(4))
    bars = setup()
    result = simulate(bars)
    assert not result.fills and result.rejections[0].reason == "risk_budget"
    with pytest.raises(ValueError, match="192 hours"):
        simulate(bars, start=bars[191].open_time)
    assert simulate(bars, start=bars[192].open_time).start == bars[192].open_time


def test_context_option_is_limited_to_budget_candidate(tmp_path: Path) -> None:
    bars = setup()
    for lookback in (0, 48, 168):
        with pytest.raises(ValueError, match="추세 맥락"):
            backtest.run_backtest(
                bars,
                bars[200].open_time,
                Decimal(1000000),
                costs(),
                "breakout-v1",
                breakout_entry_stop_trend_lookback=lookback,
            )
    with pytest.raises(ValueError, match="추세 맥락"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_trend_lookbacks={"candidate": 168},
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_budget=True,
            entry_stop_trend_context=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_experiment50_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    bars = declining_path()
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
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
    write_json(ref / "protocol.json", {"experiment": "50"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-budget"):
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
                **(OptionalModeOptions(**MODES) if filtered else {}),
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_trend_context=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_trend_context=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.entry-stop-trend-context.json").read_text()
        )
        assert result["fills"][1]["time"] > bars[205].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "51"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
