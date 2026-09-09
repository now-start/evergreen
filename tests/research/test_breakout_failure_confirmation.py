import json
from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS


def test_two_closes_are_consecutive_and_keep_original_signal_anchor():
    bars = fixture()
    bars[200] = bars[200].model_copy(update={"high": Decimal(200)})
    for i, close in ((210, 100), (211, 101), (212, 100), (213, 100)):
        bars[i] = bars[i].model_copy(update={"close": Decimal(close), "low": Decimal(99)})
    args = (bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    for delay in (0, 1):
        result = run_backtest(
            *args,
            breakout_failure_exit=True,
            breakout_failure_confirmations=2,
            extra_delay_bars=delay,
        )
        assert result.fills[0].time == bars[200 + delay].open_time
        assert result.fills[1].time == bars[214 + delay].open_time
        assert result.fills[1].reason == "signal"
    before = run_backtest(*args, breakout_failure_exit=True, breakout_failure_confirmations=2)
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(10000)})
    after = run_backtest(*args, breakout_failure_exit=True, breakout_failure_confirmations=2)
    assert before.fills[:2] == after.fills[:2]


def test_confirmation_excludes_pre_fill_bar_and_does_not_delay_original_exit():
    bars = fixture()
    # With +1h execution, bar200 is before the actual buy at201.
    for i in (200, 201, 202):
        bars[i] = bars[i].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
        breakout_failure_confirmations=2,
        extra_delay_bars=1,
    )
    assert result.fills[0].time == bars[201].open_time
    assert result.fills[1].time == bars[204].open_time
    # Original48-low exit is immediate even though this is the first close below anchor.
    bars = fixture()
    bars[250] = bars[250].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
        breakout_failure_confirmations=2,
    )
    assert result.fills[1].time == bars[251].open_time
    assert result.fills[1].reason == "signal"
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
        breakout_failure_confirmations=2,
    )
    assert result.halted and result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[251].open_time


@pytest.mark.parametrize("enabled,count", [(False, 2), (True, 0), (True, 3)])
def test_invalid_confirmation_options_are_rejected(enabled, count):
    bars = fixture()
    with pytest.raises(ValueError, match="확인"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_failure_exit=enabled,
            breakout_failure_confirmations=count,
        )


def test_new_fill_resets_the_ceiling_and_confirmation_window():
    bars = fixture()
    bars[200] = bars[200].model_copy(update={"high": Decimal(200)})
    for i in (210, 211):
        bars[i] = bars[i].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[220] = bars[220].model_copy(update={"close": Decimal(201), "high": Decimal(202)})
    for i in (221, 222, 223):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(202),
                "close": Decimal(199),
                "high": Decimal(204),
                "low": Decimal(198),
            }
        )
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
        breakout_failure_confirmations=2,
    )
    assert [(fill.side, fill.time) for fill in result.fills] == [
        ("buy", bars[200].open_time),
        ("sell", bars[212].open_time),
        ("buy", bars[221].open_time),
        ("sell", bars[223].open_time),
    ]


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("buffered", [False, True])
def test_confirmed_failure_requires_old_candidate_parity(tmp_path, monkeypatch, corrupt, buffered):
    bars = fixture()
    for i in (210, 211):
        bars[i] = bars[i].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    if buffered:
        bars[214] = bars[214].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    controls = (
        ("breakout-v1", "failed-breakout", "failed-breakout-2h")
        if buffered
        else ("breakout-v1", "failed-breakout")
    )
    candidate = "failed-breakout-tr24" if buffered else "failed-breakout-2h"
    options = {"volatility_failure_exit": True} if buffered else {"confirmed_failure_exit": True}
    start = bars[200].open_time

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(breakout_confirmation, "contiguous_blocks", blocks)
    reference = tmp_path / "ref"
    directory = reference / "seed-17/continuous/block-000"
    directory.mkdir(parents=True)
    (reference / "datasets").mkdir()
    write_json(reference / "datasets/coverage.json", {"fixture": True})
    write_json(reference / "protocol.json", {"experiment": "31" if buffered else "30"})
    write_json(reference / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        reference / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in controls:
            filtered = name != "breakout-v1"
            row = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                breakout_failure_exit=filtered,
                breakout_failure_confirmations=2 if name == "failed-breakout-2h" else 1,
                regimes={b.close_time: 2 for b in bars if b.close_time >= start}
                if filtered
                else None,
                regime_policy="breakout-filter" if filtered else "routing",
            ).model_dump(mode="json")
            if corrupt and name == controls[-1]:
                row["net_return"] = "123"
            write_json(directory / f"{scenario}.{name}.json", row)
    output = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            breakout_confirmation.run_study(tmp_path, reference, output, **options)
        assert (output / "failure.json").exists() and not (output / "status.json").exists()
    else:
        breakout_confirmation.run_study(tmp_path, reference, output, **options)
        result = json.loads(
            (output / f"seed-17/continuous/block-000/base.{candidate}.json").read_text()
        )
        assert result["fills"][1]["time"] == bars[
            215 if buffered else 212
        ].open_time.isoformat().replace("+00:00", "Z")
        status = json.loads((output / "status.json").read_text())
        assert status["baseline_parity"] and not status["live_enabled"]
        protocol = json.loads((output / "protocol.json").read_text())
        assert protocol["experiment"] == ("32" if buffered else "31")
        assert protocol["candidate"] == candidate
        assert len(list((output / "seed-17/continuous/block-000").glob("*.json"))) == (
            25 if buffered else 21
        )
