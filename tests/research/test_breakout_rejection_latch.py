import json
from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.market import write_json
from evergreen.research import backtest
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(bars, scores, *, latch=True, delay=0, capital=1000000, **kwargs):
    return run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        costs(),
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
        breakout_rejection_latch=latch,
        **kwargs,
    )


def setup():
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[220] = bars[220].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    scores = dict.fromkeys((b.close_time for b in bars[199:]), 2)
    scores[bars[199].close_time] = 0
    return bars, scores


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("reset", [Decimal(101), Decimal("101.001"), Decimal(103)])
def test_latch_freezes_prior_ceiling_until_close_at_or_below(delay, reset):
    bars, scores = setup()
    bars[225] = bars[225].model_copy(update={"close": reset, "low": Decimal(100)})
    bars[240] = bars[240].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    control = simulate(bars, scores, latch=False, delay=delay)
    assert control.fills[0].time == bars[221 + delay].open_time
    result = simulate(bars, scores, delay=delay)
    if reset == 101:
        assert result.fills[0].time == bars[241 + delay].open_time
    else:
        assert not result.fills  # Signal-bar high104 and later ceiling107 cannot reset101.


def test_filter_denial_without_raw_breakout_does_not_latch():
    bars, scores = setup()
    bars[199] = bars[199].model_copy(update={"close": Decimal(100)})
    result = simulate(bars, scores)
    assert result.fills[0].time == bars[221].open_time


@pytest.mark.parametrize("delay", [0, 1])
def test_accepted_entry_pending_and_holding_do_not_change_control(delay, monkeypatch):
    bars, scores = setup()
    calls = []
    original_target = backtest.signal_target

    def tracked(history, holding, strategy, **kwargs):
        calls.append(history[-1].close_time)
        return original_target(history, holding, strategy, **kwargs)

    monkeypatch.setattr(backtest, "signal_target", tracked)
    scores[bars[199].close_time] = 2
    bars[200] = bars[200].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    scores[bars[200].close_time] = 0
    scores[bars[220].close_time] = 0
    bars[250] = bars[250].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    control = simulate(bars, scores, latch=False, delay=delay)
    assert simulate(bars, scores, delay=delay) == control
    assert bars[200].close_time not in calls and bars[220].close_time not in calls
    assert control.fills[0].time == bars[200 + delay].open_time
    assert control.fills[1].reason == "signal"
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    risk = simulate(bars, scores, delay=delay)
    assert risk.halted and risk.fills[1].reason == "risk"
    small = simulate(bars, scores, capital=1000)
    assert small.rejections and not small.fills


def test_reset_can_reject_again_and_each_account_starts_without_latch():
    bars, scores = setup()
    bars[225] = bars[225].model_copy(update={"close": Decimal(101), "low": Decimal(100)})
    bars[230] = bars[230].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    bars[240] = bars[240].model_copy(update={"close": Decimal(114), "high": Decimal(115)})
    for i in range(231, 240):
        bars[i] = bars[i].model_copy(update={"close": Decimal(108), "high": Decimal(109)})
    scores[bars[230].close_time] = 0
    assert not simulate(bars, scores).fills
    scores[bars[199].close_time] = 2
    assert simulate(bars, scores).fills[0].time == bars[200].open_time


@pytest.mark.parametrize(
    "kwargs",
    [
        {"breakout_trailing_exit": True},
        {"breakout_failure_exit": True},
        {"breakout_cooldown_hours": 24},
        {"breakout_exit_lookback": 24},
        {"breakout_risk_budget": True},
    ],
)
def test_latch_rejects_other_experiments(kwargs):
    bars, scores = setup()
    with pytest.raises(ValueError, match="거절"):
        simulate(bars, scores, **kwargs)


def test_latch_rejects_nonfilter_strategy_and_unknown_candidate(tmp_path):
    bars, _ = setup()
    with pytest.raises(ValueError, match="거절"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_rejection_latch=True,
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path, tmp_path, tmp_path / "out", rejection_latch=True, tr_expansion=True
        )
    with pytest.raises(ValueError, match="거절"):
        evaluate(
            {name: [] for name in ("cash", "breakout-v1", "prior")},
            tmp_path,
            models=(),
            rejection_latch_models=("cash",),
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_latch_pipeline_preserves_both_filter_controls(tmp_path, monkeypatch, corrupt):
    bars, _ = setup()
    start = bars[200].open_time

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
    write_json(ref / "protocol.json", {"experiment": "38"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "tr-expansion-24-168", "tr-compression-24-168"):
            filtered = name != "breakout-v1"
            schedule = (
                runner.compression_schedule
                if name == "tr-compression-24-168"
                else runner.expansion_schedule
            )
            result = run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes=schedule(bars, start) if filtered else None,
                regime_policy="breakout-filter" if filtered else "routing",
            ).model_dump(mode="json")
            if corrupt and name == "tr-compression-24-168":
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, rejection_latch=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, rejection_latch=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.expansion-rejection-latch.json").read_text()
        )
        assert not result["fills"]
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "39"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
        assert len(list((out / "seed-17/continuous/block-000").glob("*.json"))) == 25
