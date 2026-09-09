import json
from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS


@pytest.mark.parametrize("low,expected", [(99, 2), (98, 0), (100, 2)])
def test_compression_boundary_and_current_future_exclusion(low, expected):
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[198] = bars[198].model_copy(update={"low": Decimal(low)})
    start = bars[200].open_time
    assert runner.compression_schedule(bars, start)[start] == expected
    bars[199] = bars[199].model_copy(update={"high": Decimal(10000), "low": Decimal(1)})
    bars[200] = bars[200].model_copy(update={"high": Decimal(20000)})
    assert runner.compression_schedule(bars, start)[start] == expected
    early = bars[:169]
    assert runner.compression_schedule(early, early[0].close_time) == dict.fromkeys(
        (b.close_time for b in early), 0
    )


def test_zero_range_approval_still_requires_original_breakout():
    bars = [
        b.model_copy(update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100)))
        for b in fixture()
    ]
    start = bars[200].open_time
    scores = runner.compression_schedule(bars, start)
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


@pytest.mark.parametrize("delay", [0, 1])
def test_compression_keeps_channel_exit_risk_and_minimum_order(delay):
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[210] = bars[210].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    start = bars[200].open_time
    scores = runner.compression_schedule(bars, start)
    assert scores[start] == 2
    scores[bars[250].close_time] = 0
    args = (bars, start, Decimal(1000000), costs())
    original = run_backtest(*args, "breakout-v1", extra_delay_bars=delay)
    result = run_backtest(
        *args,
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
    )
    assert result.fills == original.fills
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


def test_compression_rejects_combined_modes_before_output(tmp_path):
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path, tmp_path, tmp_path / "out", tr_compression=True, tr_expansion=True
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_compression_pipeline_reproduces_expansion_control(tmp_path, monkeypatch, corrupt):
    bars = fixture()
    # Recent TR pulse rejects the first breakout; once it ages out, a new one can pass.
    bars[180] = bars[180].model_copy(update={"low": Decimal(60)})
    bars[220] = bars[220].model_copy(update={"high": Decimal(107), "close": Decimal(106)})
    start = bars[200].open_time
    scores = runner.compression_schedule(bars, start)
    assert scores[start] == 0 and scores[bars[220].close_time] == 2

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(runner, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    folder = ref / "seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "37"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "tr-expansion-24-168"):
            filtered = name != "breakout-v1"
            result = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes=runner.expansion_schedule(bars, start) if filtered else None,
                regime_policy="breakout-filter" if filtered else "routing",
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, tr_compression=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, tr_compression=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.tr-compression-24-168.json").read_text()
        )
        assert result["fills"][0]["time"] == bars[221].open_time.isoformat().replace("+00:00", "Z")
        assert len(result["fills"]) == 2 and result["fills"][1]["reason"] == "settlement"
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "38"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
