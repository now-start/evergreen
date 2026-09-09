import json
from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.breakout_features import prior_mean_true_range
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS


def test_weekly_true_range_includes_first_previous_close_and_excludes_signal():
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[30] = bars[30].model_copy(update={"close": Decimal(90), "low": Decimal(89)})
    expected = Decimal(345) / 168  # First TR11, then167 TRs of2.
    assert prior_mean_true_range(bars[:200], lookback=168) == expected
    assert prior_mean_true_range(bars[30:200], lookback=168) == expected
    assert prior_mean_true_range(bars[:200]) == 2
    bars[29] = bars[29].model_copy(update={"close": Decimal(1)})
    bars[199] = bars[199].model_copy(update={"high": Decimal(10000), "low": Decimal(1)})
    assert prior_mean_true_range(bars[:200], lookback=168) == expected
    with pytest.raises(ValueError, match="170"):
        prior_mean_true_range(bars[31:200], lookback=168)
    with pytest.raises(ValueError, match="24/168"):
        prior_mean_true_range(bars, lookback=0)


@pytest.mark.parametrize(
    "last_low,expected", [(Decimal(99), 0), (Decimal(98), 2), (Decimal(100), 0)]
)
def test_expansion_strict_boundary_and_current_future_exclusion(last_low, expected):
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[198] = bars[198].model_copy(update={"low": last_low})
    start = bars[200].open_time
    assert breakout_confirmation.expansion_schedule(bars, start)[start] == expected
    bars[199] = bars[199].model_copy(update={"high": Decimal(10000), "low": Decimal(1)})
    bars[200] = bars[200].model_copy(update={"high": Decimal(20000)})
    assert breakout_confirmation.expansion_schedule(bars, start)[start] == expected
    early = bars[:169]
    assert breakout_confirmation.expansion_schedule(early, early[0].close_time) == dict.fromkeys(
        (b.close_time for b in early), 0
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_expansion_preserves_original_exit_risk_and_order_size(delay):
    bars = fixture()
    bars[210] = bars[210].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    start = bars[200].open_time
    args = (bars, start, Decimal(1000000), costs())
    original = run_backtest(*args, "breakout-v1", extra_delay_bars=delay)
    scores = breakout_confirmation.expansion_schedule(bars, start)
    scores[bars[250].close_time] = 0
    result = run_backtest(
        *args,
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
    )
    assert result.fills == original.fills
    assert result.fills[0].time == bars[200 + delay].open_time
    assert result.fills[1].time == bars[251 + delay].open_time
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = run_backtest(
        *args,
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
    )
    assert result.halted and result.fills[1].reason == "risk"
    small = run_backtest(
        bars,
        start,
        Decimal(1000),
        costs(),
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
    )
    assert small.rejections and not small.fills


def test_expansion_zero_range_and_approval_do_not_manufacture_breakout():
    bars = [
        b.model_copy(update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100)))
        for b in fixture()
    ]
    start = bars[200].open_time
    assert breakout_confirmation.expansion_schedule(bars, start)[start] == 0
    bars[198] = bars[198].model_copy(update={"high": Decimal(101), "low": Decimal(99)})
    scores = breakout_confirmation.expansion_schedule(bars, start)
    assert scores[start] == 2
    result = run_backtest(
        bars,
        start,
        Decimal(1000000),
        costs(),
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
    )
    assert not result.fills


def test_expansion_rejects_combined_runner_modes(tmp_path):
    with pytest.raises(ValueError, match="동시"):
        breakout_confirmation.run_study(
            tmp_path, tmp_path, tmp_path / "out", tr_expansion=True, adaptive_trailing_exit=True
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_expansion_pipeline_delays_entry_and_keeps_adaptive_only_in_control(
    tmp_path, monkeypatch, corrupt
):
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[220] = bars[220].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    for i in range(230, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(109),
                "high": Decimal(111),
                "low": Decimal(108),
                "close": Decimal(109),
            }
        )
    bars[230] = bars[230].model_copy(
        update={"open": Decimal(104), "close": Decimal(120), "high": Decimal(121)}
    )
    bars[231] = bars[231].model_copy(update={"open": Decimal(120), "high": Decimal(121)})
    start = bars[200].open_time

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(breakout_confirmation, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    folder = ref / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "36"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "adaptive-trailing-tr24"):
            filtered = name != "breakout-v1"
            r = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes=dict.fromkeys((b.close_time for b in bars if b.close_time >= start), 2)
                if filtered
                else None,
                regime_policy="breakout-filter" if filtered else "routing",
                breakout_trailing_exit=filtered,
                breakout_trailing_profit_only=filtered,
                breakout_trailing_adaptive=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                r["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", r)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            breakout_confirmation.run_study(tmp_path, ref, out, tr_expansion=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        breakout_confirmation.run_study(tmp_path, ref, out, tr_expansion=True)
        r = json.loads(
            (out / "seed-17/continuous/block-000/base.tr-expansion-24-168.json").read_text()
        )
        assert len(r["fills"]) == 2 and r["fills"][0]["time"] == bars[
            221
        ].open_time.isoformat().replace("+00:00", "Z")
        assert r["fills"][1]["reason"] == "settlement" and not r["halted"]
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "37"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
        assert len(list((out / "seed-17/continuous/block-000").glob("*.json"))) == 21
