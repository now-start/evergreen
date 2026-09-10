from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path
from typing import cast

import pytest
from test_breakout_entry_stop_trend_context import MODES, OptionalModeOptions
from test_breakout_liquidation_buffer import path
from test_strategies import from_closes

from evergreen.market import Candle, write_json
from evergreen.research.backtest import run_backtest
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.regime import SCENARIOS
from evergreen.strategies import Strategy


@pytest.mark.parametrize("name", ["rsi-rebound-v1", "band-reversion-v1"])
@pytest.mark.parametrize("delay", [0, 1])
def test_candidates_use_existing_signal_and_execution_without_breakout_guards(
    name: str, delay: int
) -> None:
    from evergreen.research.experiments.strategy_family import simulate

    bars = path()
    start = bars[200].open_time
    actual = simulate(bars, start, name, costs(2, 2), delay)
    assert actual == run_backtest(
        bars, start, D(1000000), costs(2, 2), cast(Strategy, name), extra_delay_bars=delay
    )


def test_gate_uses_paired_median_and_all_risk_checks() -> None:
    from evergreen.research.experiments.strategy_family import summarize

    bars = path()
    base = run_backtest(bars, bars[200].open_time, D(1000000), costs(), "cash")
    baseline = [base.model_copy(update={"net_return": D(".1")}) for _ in range(3)]
    candidate = [
        base.model_copy(update={"net_return": v, "natural_exits": 10})
        for v in (D(".1"), D(".1"), D(".5"))
    ]
    assert not summarize(candidate, baseline, "base")["gate"]
    candidate = [r.model_copy(update={"net_return": D(".11")}) for r in candidate]
    assert summarize(candidate, baseline, "base")["gate"]
    for changes in ({"halted": True}, {"max_drawdown": D(".100001")}, {"natural_exits": 0}):
        assert not summarize([r.model_copy(update=changes) for r in candidate], baseline, "base")[
            "gate"
        ]
    with pytest.raises(ValueError, match="계좌"):
        summarize(candidate, baseline[:2], "base")
    with pytest.raises(ValueError, match="계좌"):
        summarize(
            candidate, [r.model_copy(update={"extra_delay_bars": 1}) for r in baseline], "base"
        )


@pytest.mark.parametrize("name", ["rsi-rebound-v1", "band-reversion-v1"])
def test_candidate_parity_contains_actual_delayed_round_trip(name: str) -> None:
    from evergreen.research.experiments.strategy_family import simulate

    values = (
        [D(100)] * 185 + [D(110)] + [D(100)] * 13 + [D(101), D(105)] + [D(105)] * 8
        if name == "rsi-rebound-v1"
        else [D(100)] * 176 + [D(99), D(101)] * 12 + [D(97)] * 3 + [D(100)] * 8
    )
    bars = from_closes(values)
    start = bars[200].open_time
    result = simulate(bars, start, name, costs(), 1)
    assert result == run_backtest(
        bars, start, D(1000000), costs(), cast(Strategy, name), extra_delay_bars=1
    )
    assert len(result.fills) >= 2 and result.natural_exits >= 1
    assert result.fills[0].side == "buy" and result.fills[1].side == "sell"


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_matches_all_controls_and_blocks_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from evergreen.research.experiments import strategy_family as runner

    bars = path()
    start = bars[200].open_time

    def blocks(raw: Path, out: Path) -> list[list[Candle]]:
        out.mkdir()
        write_json(out / "coverage.json", {"fixture": True})
        return [bars]

    monkeypatch.setattr(runner, "contiguous_blocks", blocks)
    ref = tmp_path / "ref"
    directory = ref / "seed-17/continuous/block-000"
    directory.mkdir(parents=True)
    (ref / "datasets").mkdir()
    write_json(ref / "datasets/coverage.json", {"fixture": True})
    write_json(ref / "protocol.json", {"experiment": "60"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in runner.CONTROLS:
            filtered = name in ("entry-stop-budget", "liquidation-buffer")
            expected = run_backtest(
                bars,
                start,
                D(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else cast(Strategy, name),
                extra_delay_bars=delay,
                regime_policy="breakout-filter" if filtered else "routing",
                regimes={b.close_time: 2 for b in bars[199:]} if filtered else None,
                breakout_liquidation_buffer=name == "liquidation-buffer",
                **(OptionalModeOptions(**MODES) if filtered else {}),
            ).model_dump(mode="json")
            if corrupt and name == "entry-stop-budget":
                expected["total_fees"] = "123"
            write_json(directory / f"{scenario}.{name}.json", expected)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="대조"):
            runner.run_study(tmp_path, ref, out)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out)
        status = json.loads((out / "status.json").read_text())
        assert status["control_comparisons"] == 16 and status["pnl_checks"] == 24
        assert not status["live_enabled"]
        for name in runner.CANDIDATES:
            actual = json.loads(
                (out / f"seed-17/continuous/block-000/base.{name}.json").read_text()
            )
            assert actual == run_backtest(
                bars, start, D(1000000), costs(), cast(Strategy, name)
            ).model_dump(mode="json")
        with pytest.raises(FileExistsError):
            runner.run_study(tmp_path, ref, out)
