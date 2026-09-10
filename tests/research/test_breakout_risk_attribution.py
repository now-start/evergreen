import json
import sys
from decimal import Decimal

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_trend_context import MODES

from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_risk_attribution as runner
from evergreen.research.experiments.breakout_meta import costs as trading_costs
from evergreen.research.experiments.breakout_risk_attribution import first_breach, observe


def test_observer_matches_result_and_does_not_change_profile():
    bars = setup()
    start, costs = bars[200].open_time, trading_costs()
    expected = run_backtest(bars, start, Decimal(1000000), costs, "breakout-v1")
    before = sys.getprofile()
    actual, marks = observe(
        lambda: run_backtest(bars, start, Decimal(1000000), costs, "breakout-v1")
    )
    assert actual == expected
    assert sys.getprofile() is before
    assert len(marks) == len(actual.equity_curve)
    assert [m["equity"] for m in marks] == [str(p.equity) for p in actual.equity_curve]
    assert all(
        m["pending_phase"] == "before_close_decision" for m in marks if m["phase"] == "close"
    )


def test_observer_restores_profile_on_failure():
    def fail():
        raise ValueError("expected failure")

    before = sys.getprofile()
    with pytest.raises(ValueError, match="expected failure"):
        observe(fail)
    assert sys.getprofile() is before


def test_observer_refuses_existing_profiler():
    def profiler(frame, event, arg):
        return None

    previous = sys.getprofile()
    try:
        sys.setprofile(profiler)
        with pytest.raises(ValueError, match="프로파일러"):
            observe(lambda: None)
        assert sys.getprofile() is profiler
    finally:
        sys.setprofile(previous)


@pytest.mark.parametrize("delay", [0, 1])
def test_risk_trace_retains_pending_and_fill_costs(delay):
    bars = setup()
    start, costs = bars[200].open_time, trading_costs()
    # A large fall after the buy crosses the account limit, independently of exit signals.
    bars[202] = bars[202].model_copy(update={"close": Decimal(80), "low": Decimal(79)})
    for i in range(203, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(80),
                "high": Decimal(81),
                "low": Decimal(79),
                "close": Decimal(80),
            }
        )
    result, marks = observe(
        lambda: run_backtest(
            bars, start, Decimal(1000000), costs, "breakout-v1", extra_delay_bars=delay
        )
    )
    breach = next(m for m in marks if Decimal(m["drawdown"]) >= Decimal(".1"))
    assert breach["btc"] != "0"
    assert breach["phase"] == "close"
    risk = next(f for f in result.fills if f.reason == "risk")
    fill_mark = next(
        m for m in marks if m["time"] == risk.time.isoformat() and m["phase"] == "open"
    )
    assert Decimal(fill_mark["fill_cost"]) == risk.fee + risk.slippage_cost
    assert (
        Decimal(fill_mark["equity_before_fill_cost"])
        == Decimal(fill_mark["equity"]) + risk.fee + risk.slippage_cost
    )
    assert Decimal(fill_mark["btc"]) == 0
    if delay:
        assert any(m["pending"] and m["pending"][2] == "risk" for m in marks)
    assert first_breach(result, marks)["classification"] == "holding_price_crossing"


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_cost_only_breach_including_boundary_settlement(side):
    bars = setup()
    costs = trading_costs().model_copy(update={f"{side}_fee": Decimal(".2")})
    result, marks = observe(
        lambda: run_backtest(bars, bars[200].open_time, Decimal(1000000), costs, "breakout-v1")
    )
    finding = first_breach(result, marks)
    assert finding["classification"] == "fill_cost_crossing"
    assert finding["first"]["fill"]["side"] == side
    if side == "sell":
        assert finding["first"]["phase"] == "settlement"
        assert Decimal(finding["first"]["btc"]) == 0


def test_trace_rejects_corrupt_curve_and_halted_flag():
    bars = setup()
    result, marks = observe(
        lambda: run_backtest(
            bars, bars[200].open_time, Decimal(1000000), trading_costs(), "breakout-v1"
        )
    )
    assert first_breach(result, marks) is None
    with pytest.raises(ValueError, match="중단"):
        first_breach(result.model_copy(update={"halted": True}), marks)
    marks[0]["phase"] = "settlement"
    with pytest.raises(ValueError, match="곡선"):
        first_breach(result, marks)


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_replays_all_halted_cases_and_rejects_result_drift(tmp_path, monkeypatch, corrupt):
    bars = setup()
    bars[202] = bars[202].model_copy(update={"close": Decimal(80), "low": Decimal(79)})
    start = bars[200].open_time
    source, output = tmp_path / "source", tmp_path / "output"

    def save(name, value):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    save("protocol.json", {"experiment": "55"})
    save("status.json", {"status": "completed_partial_coverage"})
    save("intervals.json", [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}])
    save("datasets/block-000/candles.jsonl", [])
    save("datasets/block-000/quality.json", {})
    monkeypatch.setattr(runner, "load_dataset", lambda d: (bars, "fixture"))
    monkeypatch.setattr(runner, "SCENARIOS", [("base", 1, 1, 0)])
    for model in ("breakout-v1", "cash", "prior", *runner.MODELS):
        options = {
            **MODES,
            "breakout_entry_stop_trend_lookback": 168
            if model == "entry-stop-trend-context"
            else 24,
        }
        result = run_backtest(
            bars,
            start,
            Decimal(1000000),
            trading_costs(),
            "regime-mlp-v1",
            regimes={b.close_time: 2 for b in bars if b.close_time >= start},
            regime_policy="breakout-filter",
            breakout_extension_floor=model == "extension-floor-2h",
            **options,
        ).model_dump(mode="json")
        if corrupt and model == "entry-stop-budget":
            result["total_fees"] = "0"
        save(f"seed-17/continuous/block-000/base.{model}.json", result)
    if corrupt:
        with pytest.raises(ValueError, match="전체 결과"):
            runner.analyze(source, output)
        assert not (output / "status.json").exists()
        assert (output / "failure.json").exists()
    else:
        runner.analyze(source, output)
        status = json.loads((output / "status.json").read_text())
        assert status["reconciled"] == 6
        assert status["replay_comparisons"] == status["breaches"] == 3
        assert status["live_enabled"] is False
