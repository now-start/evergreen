from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_breakout_entry_stop import setup

from evergreen.market import Candle, write_json
from evergreen.research.backtest import Result, run_backtest
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(
    bars: list[Candle],
    *,
    confirmations: int = 2,
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
        breakout_entry_stop_confirmations=confirmations,
        extra_delay_bars=delay,
        **kwargs,
    )


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("reset", [Decimal(0), Decimal(".01")])
def test_two_strict_closes_reset_and_pending_rebound_does_not_cancel(
    delay: int, reset: Decimal
) -> None:
    bars = setup()
    if delay:
        bars[201] = bars[201].model_copy(update={"open": Decimal(108), "high": Decimal(109)})
    stop = bars[200 + delay].open - 3 * prior_mean_true_range(bars[:200])
    for i, close in ((203, stop - 1), (204, stop + reset), (205, stop - 1), (206, stop - 1)):
        bars[i] = bars[i].model_copy(update={"close": close, "low": Decimal(1)})
    result = simulate(bars, delay=delay)
    assert result.fills[0].reference_price == bars[200 + delay].open
    assert result.fills[1].time == bars[207 + delay].open_time
    assert result.fills[1].signal_time == bars[206].close_time
    assert result.fills[1].reason == "signal"
    assert simulate(bars, confirmations=1, delay=delay).fills[1].time == bars[204 + delay].open_time


def test_delayed_buy_does_not_count_preentry_close() -> None:
    bars = setup()
    for i in (200, 201, 202):
        bars[i] = bars[i].model_copy(update={"close": Decimal(101), "low": Decimal(1)})
    bars[201] = bars[201].model_copy(update={"open": Decimal(108), "high": Decimal(109)})
    result = simulate(bars, delay=1)
    assert result.fills[0].time == bars[201].open_time
    assert result.fills[1].signal_time == bars[202].close_time
    assert result.fills[1].time == bars[204].open_time


@pytest.mark.parametrize("risk", [False, True])
def test_channel_and_risk_exit_do_not_wait_for_confirmation(risk: bool) -> None:
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


def test_new_buy_resets_threshold_and_confirmation_window() -> None:
    bars = setup()
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(1)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    bars[251] = bars[251].model_copy(
        update={
            "open": Decimal(112),
            "high": Decimal(113),
            "low": Decimal(100),
            "close": Decimal(104),
        }
    )
    stop = Decimal(112) - 3 * prior_mean_true_range(bars[:251])
    assert stop > 104
    bars[252] = bars[252].model_copy(
        update={"close": stop, "high": Decimal(113), "low": Decimal(100)}
    )
    for i in (253, 254):
        bars[i] = bars[i].model_copy(
            update={"close": stop - 1, "high": Decimal(113), "low": Decimal(100)}
        )
    result = simulate(bars)
    assert [f.time for f in result.fills] == [bars[i].open_time for i in (200, 205, 251, 255)]
    assert result.fills[-1].signal_time == bars[254].close_time
    unfilled = simulate(setup(), capital=1000)
    assert not unfilled.fills and unfilled.rejections


@pytest.mark.parametrize("wide", [False, True])
def test_zero_tr_or_nonpositive_threshold_adds_no_stop(wide: bool) -> None:
    bars = [
        b.model_copy(update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100)))
        for b in setup()
    ]
    if wide:
        for i in range(175, 199):
            bars[i] = bars[i].model_copy(update={"high": Decimal(200), "low": Decimal(1)})
    entry = Decimal(204 if wide else 104)
    bars[199] = bars[199].model_copy(update={"close": entry - 1, "high": entry, "low": Decimal(80)})
    for i in range(200, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": entry, "close": entry, "high": entry + 1, "low": entry - 1}
        )
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": entry - 6, "low": entry - 7})
    assert simulate(bars) == simulate(bars, confirmations=1)


def test_confirmation_rejects_invalid_options_and_unbound_models(tmp_path: Path) -> None:
    bars = setup()
    for count in (0, 3):
        with pytest.raises(ValueError, match="고정 손절 확인"):
            simulate(bars, confirmations=count)
    with pytest.raises(ValueError, match="고정 손절 확인"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop_confirmations=2,
        )
    for option in ("breakout_entry_stop_floor", "breakout_entry_stop_expansion"):
        with pytest.raises(ValueError, match="고정 손절 확인"):
            simulate(bars, **{option: True})
    with pytest.raises(ValueError, match="고정 손절 확인"):
        evaluate(
            {n: [] for n in ("cash", "breakout-v1", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_confirmations={"candidate": 2},
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_expansion=True,
            confirmed_entry_stop=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_confirmation_pipeline_preserves_expansion_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    bars = setup()
    for i in (203, 204):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(1)})
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
    write_json(ref / "protocol.json", {"experiment": "42"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-expansion"):
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
                breakout_entry_stop_expansion=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, confirmed_entry_stop=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, confirmed_entry_stop=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.entry-stop-confirm2.json").read_text()
        )
        assert result["fills"][1]["time"] == bars[205].open_time.isoformat().replace("+00:00", "Z")
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "43"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
