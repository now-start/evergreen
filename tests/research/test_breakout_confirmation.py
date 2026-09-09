import json
from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_confirmation import confirmation_schedule
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS


def test_wick_boundary_body_direction_and_future_invariance():
    bars = fixture()
    start = bars[200].open_time
    bars[199] = bars[199].model_copy(update={"low": Decimal(100)})
    assert breakout_confirmation.wick_schedule(bars, start)[start] == 2
    bars[199] = bars[199].model_copy(update={"high": Decimal("104.1")})
    assert breakout_confirmation.wick_schedule(bars, start)[start] == 0
    bars[199] = bars[199].model_copy(
        update={
            "open": Decimal(104),
            "close": Decimal(103),
            "high": Decimal(105),
            "low": Decimal(101),
        }
    )
    assert breakout_confirmation.wick_schedule(bars, start)[start] == 2
    before = breakout_confirmation.wick_schedule(bars, start)
    bars[200] = bars[200].model_copy(update={"high": Decimal(10000)})
    assert breakout_confirmation.wick_schedule(bars, start)[start] == before[start]
    bars[199] = bars[199].model_copy(
        update=dict.fromkeys(("open", "close", "high", "low"), Decimal(100))
    )
    assert breakout_confirmation.wick_schedule(bars, start)[start] == 2


def test_wick_filter_preserves_next_open_exit_and_ignores_volume():
    bars = [b.model_copy(update={"volume": Decimal(0)}) for b in fixture()]
    start = bars[200].open_time
    # Both price exit and long-wick rejection at 250: rejection must not prevent a sell.
    bars[250] = bars[250].model_copy(
        update={"high": Decimal(120), "low": Decimal(97), "close": Decimal(98)}
    )
    scores = breakout_confirmation.wick_schedule(bars, start)
    assert scores[bars[250].close_time] == 0
    for delay in (0, 1):
        result = run_backtest(
            bars,
            start,
            Decimal(1000000),
            costs(),
            "regime-mlp-v1",
            regimes=scores,
            regime_policy="breakout-filter",
            extra_delay_bars=delay,
        )
        assert result.fills[0].time == bars[200 + delay].open_time
        assert result.fills[1].time == bars[251 + delay].open_time
        assert result.fills[1].reason == "signal"
    bars[199] = bars[199].model_copy(update={"high": Decimal(120)})
    blocked = run_backtest(
        bars,
        start,
        Decimal(1000000),
        costs(),
        "regime-mlp-v1",
        regimes=breakout_confirmation.wick_schedule(bars, start),
        regime_policy="breakout-filter",
    )
    assert not blocked.fills


def test_confirmation_filters_cannot_be_combined(tmp_path):
    with pytest.raises(ValueError, match="동시"):
        breakout_confirmation.run_study(
            tmp_path, tmp_path, tmp_path / "out", volume_confirmation=True, wick_confirmation=True
        )
    assert not (tmp_path / "out").exists()


def test_volume_confirmation_is_causal_strict_and_does_not_delay_entry():
    bars = [b.model_copy(update={"volume": Decimal(10)}) for b in fixture()]
    bars[199] = bars[199].model_copy(update={"volume": Decimal(11)})
    start = bars[200].open_time
    scores = breakout_confirmation.volume_schedule(bars, start)
    assert scores[bars[199].close_time] == 2
    for delay in (0, 1):
        result = run_backtest(
            bars,
            start,
            Decimal(1000000),
            costs(),
            "regime-mlp-v1",
            regimes=scores,
            regime_policy="breakout-filter",
            extra_delay_bars=delay,
        )
        assert result.fills[0].time == bars[200 + delay].open_time
    # Equality is blocked; the value at i-24 is included and i-25 is excluded.
    bars[199] = bars[199].model_copy(update={"volume": Decimal(10)})
    assert breakout_confirmation.volume_schedule(bars, start)[start] == 0
    bars[174] = bars[174].model_copy(update={"volume": Decimal(0)})
    assert breakout_confirmation.volume_schedule(bars, start)[start] == 0
    bars[175] = bars[175].model_copy(update={"volume": Decimal(0)})
    assert breakout_confirmation.volume_schedule(bars, start)[start] == 2
    scores = breakout_confirmation.volume_schedule(bars, start)
    bars[200] = bars[200].model_copy(update={"volume": Decimal(1000000)})
    assert breakout_confirmation.volume_schedule(bars, start)[start] == scores[start]
    zero = [b.model_copy(update={"volume": Decimal(0)}) for b in bars]
    assert breakout_confirmation.volume_schedule(zero, zero[0].close_time)[start] == 0
    zero[199] = zero[199].model_copy(update={"volume": Decimal(1)})
    assert breakout_confirmation.volume_schedule(zero, start)[start] == 2
    # Volume alone cannot manufacture a breakout.
    zero[199] = zero[199].model_copy(update={"close": Decimal(100)})
    result = run_backtest(
        zero,
        start,
        Decimal(1000000),
        costs(),
        "regime-mlp-v1",
        regimes=breakout_confirmation.volume_schedule(zero, start),
        regime_policy="breakout-filter",
    )
    assert not result.fills


def test_confirmation_uses_previous_bar_and_current_engine_condition():
    bars = fixture()
    # First breakout at 199; second breakout at 200; fill at 201, not retrospectively at 200.
    bars[200] = bars[200].model_copy(update={"high": Decimal(106), "close": Decimal(105)})
    start = bars[200].open_time
    scores = confirmation_schedule(bars, start)
    assert scores[bars[199].close_time] == 0
    assert scores[bars[200].close_time] == 2
    result = run_backtest(
        bars,
        start,
        Decimal(1000000),
        costs(),
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
    )
    assert result.fills[0].time == bars[201].open_time
    delayed = run_backtest(
        bars,
        start,
        Decimal(1000000),
        costs(),
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
        extra_delay_bars=1,
    )
    assert delayed.fills[0].time == bars[202].open_time
    changed = bars.copy()
    changed[200] = changed[200].model_copy(update={"close": Decimal(104)})
    # Previous-bar approval alone does not bypass the current breakout condition.
    other = run_backtest(
        changed,
        start,
        Decimal(1000000),
        costs(),
        "regime-mlp-v1",
        regimes=confirmation_schedule(changed, start),
        regime_policy="breakout-filter",
    )
    assert not other.fills
    changed = bars.copy()
    changed[-1] = changed[-1].model_copy(update={"high": Decimal(10000), "close": Decimal(10000)})
    assert confirmation_schedule(changed, start) == confirmation_schedule(bars, start)


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("mode", ["time", "volume", "wick", "budget"])
def test_confirmation_pipeline_requires_reference_parity(tmp_path, monkeypatch, corrupt, mode):
    volume, wick = mode == "volume", mode == "wick"
    budget = mode == "budget"
    expected = {
        "time": ("25", "26", "confirm-1h"),
        "volume": ("26", "27", "volume-24h"),
        "wick": ("27", "28", "wick-25"),
        "budget": ("28", "29", "channel-budget"),
    }[mode]
    bars = fixture()

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(breakout_confirmation, "contiguous_blocks", blocks)
    reference = tmp_path / "reference"
    folder = reference / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (reference / "datasets").mkdir()
    write_json(reference / "datasets/coverage.json", {"fixture": True})
    write_json(reference / "status.json", {"status": "completed_partial_coverage"})
    write_json(reference / "protocol.json", {"experiment": expected[0]})
    write_json(
        reference / "intervals.json",
        [{"start": bars[200].open_time.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for name, fee, slip, delay in SCENARIOS:
        row = run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(fee, slip),
            "breakout-v1",
            extra_delay_bars=delay,
        ).model_dump(mode="json")
        if corrupt:
            row["net_return"] = "123"
        write_json(folder / f"{name}.breakout-v1.json", row)
    output = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            breakout_confirmation.run_study(
                tmp_path,
                reference,
                output,
                volume_confirmation=volume,
                wick_confirmation=wick,
                risk_budget=budget,
            )
        assert (output / "failure.json").exists() and not (output / "status.json").exists()
    else:
        breakout_confirmation.run_study(
            tmp_path,
            reference,
            output,
            volume_confirmation=volume,
            wick_confirmation=wick,
            risk_budget=budget,
        )
        status = json.loads((output / "status.json").read_text())
        assert status["baseline_parity"] and not status["live_enabled"]
        protocol = json.loads((output / "protocol.json").read_text())
        assert protocol["experiment"] == expected[1]
        assert protocol["candidate"] == expected[2]
        if budget:
            candidate = json.loads(
                (output / "seed-17/continuous/block-000/base.channel-budget.json").read_text()
            )
            assert not candidate["fills"]
        assert len(list((output / "seed-17/continuous/block-000").glob("*.json"))) == 17
