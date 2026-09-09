import json
from decimal import Decimal

import pytest
from test_learning import candles

from evergreen.market import write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_exit
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS


def fixture():
    bars = [
        b.model_copy(
            update={
                "open": Decimal(100),
                "high": Decimal(101),
                "low": Decimal(99),
                "close": Decimal(100),
            }
        )
        for b in candles(260)
    ]
    bars[180] = bars[180].model_copy(update={"low": Decimal(80)})
    bars[199] = bars[199].model_copy(update={"high": Decimal(104), "close": Decimal(103)})
    for i in range(200, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(104),
                "high": Decimal(105),
                "low": Decimal(103),
                "close": Decimal(104),
            }
        )
    bars[225] = bars[225].model_copy(update={"low": Decimal(101), "close": Decimal(102)})
    bars[226] = bars[226].model_copy(update={"open": Decimal(102), "low": Decimal(101)})
    return bars


def test_fast_exit_excludes_current_low_and_preserves_default_and_delay():
    bars = fixture()
    args = (bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    original = run_backtest(*args)
    assert original == run_backtest(*args, breakout_exit_lookback=48)
    fast = run_backtest(*args, breakout_exit_lookback=24)
    assert fast.fills[0] == original.fills[0]
    assert fast.fills[1].time == bars[226].open_time
    assert fast.fills[1].reason == "signal"
    assert original.fills[1].reason == "settlement"
    delayed = run_backtest(*args, breakout_exit_lookback=24, extra_delay_bars=1)
    assert delayed.fills[1].time == bars[227].open_time
    assert fast.total_fees > 0 and fast.total_slippage > 0
    approvals = {b.close_time: 2 for b in bars[199:]}
    filtered = run_backtest(
        *args[:-1],
        "regime-mlp-v1",
        regimes=approvals,
        regime_policy="breakout-filter",
        breakout_exit_lookback=24,
    )
    assert filtered.fills == fast.fills
    for strategy, lookback in (("cash", 24), ("breakout-v1", 12)):
        with pytest.raises(ValueError, match="청산"):
            run_backtest(*args[:-1], strategy, breakout_exit_lookback=lookback)


@pytest.mark.parametrize("corrupt", [False, True])
def test_exit_study_requires_reference_parity(tmp_path, monkeypatch, corrupt):
    bars = fixture()

    def blocks(raw, out):
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(breakout_exit, "contiguous_blocks", blocks)
    reference = tmp_path / "reference"
    folder = reference / "control/seed-17/continuous/block-000"
    folder.mkdir(parents=True)
    (reference / "datasets").mkdir()
    write_json(reference / "datasets/coverage.json", {"fixture": True})
    write_json(reference / "status.json", {"status": "completed_partial_coverage"})
    write_json(reference / "protocol.json", {"experiment": "23"})
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
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            breakout_exit.run_study(tmp_path, reference, out)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        breakout_exit.run_study(tmp_path, reference, out)
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
        assert not json.loads((out / "status.json").read_text())["live_enabled"]
        assert len(list((out / "seed-17/continuous/block-000").glob("*.json"))) == 17
        followup = tmp_path / "cooldown"
        breakout_exit.run_study(tmp_path, out, followup, cooldown_hours=24)
        assert json.loads((followup / "status.json").read_text())["baseline_parity"]
        original = out / "seed-17/continuous/block-000"
        revised = followup / "seed-17/continuous/block-000"
        for name, _, _, _ in SCENARIOS:
            for control in ("breakout-v1", "exit24"):
                filename = f"{name}.{control}.json"
                assert (original / filename).read_bytes() == (revised / filename).read_bytes()
        rows = json.loads((followup / "results.json").read_text())["historical"]
        for row in rows:
            if row["candidate"] == "exit24-cooldown24":
                a = json.loads((revised / f"{row['scenario']}.exit24-cooldown24.json").read_text())
                b = json.loads((revised / f"{row['scenario']}.exit24.json").read_text())
                delta = Decimal(a["net_return"]) - Decimal(b["net_return"])
                assert Decimal(row["delta_exit24"]) == delta
                assert not row["gate"] or delta > 0
