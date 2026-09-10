import json
from decimal import Decimal

import pytest
from test_breakout_entry_stop import setup
from test_breakout_entry_stop_trend_confirmation import declining_path
from test_breakout_entry_stop_trend_context import MODES, long_decline

from evergreen.market import write_json
from evergreen.research import backtest
from evergreen.research.experiments import breakout_exit_checkpoint as runner
from evergreen.research.experiments.breakout_exit_extension import simulate as simulate_control
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(bars, *, checkpoint=True, delay=0, capital=Decimal(1000000)):
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        capital,
        costs(),
        "breakout-v1",
        extra_delay_bars=delay,
        breakout_exit_checkpoint=checkpoint,
        **MODES,
    )


def checkpoint_path():
    bars = declining_path()
    for i in range(205, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(104),
                "high": Decimal(105),
                "low": Decimal(102),
                "close": Decimal(103),
            }
        )
    return bars


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("price", [102, 103])
def test_checkpoint_uses_24h_closed_price_including_equality(monkeypatch, delay, price):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    bars[228] = bars[228].model_copy(update={"close": Decimal(price), "low": Decimal(101)})
    bars[229] = bars[229].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    control, result = simulate(bars, checkpoint=False, delay=delay), simulate(bars, delay=delay)
    assert control.fills[0] == result.fills[0]
    assert control.fills[1].signal_time == bars[204].close_time
    assert result.fills[1].signal_time == bars[228].close_time
    assert result.fills[1].time == bars[229 + delay].open_time
    assert result.fills[1].reason == "signal"


def test_recovered_checkpoint_is_consumed_once_not_a_max_holding_limit(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    bars[228] = bars[228].model_copy(update={"close": Decimal(104)})
    result = simulate(bars)
    assert result.fills[1].reason == "settlement"
    assert result.fills[1].time == bars[-1].close_time


def test_both_contexts_exit_without_extension(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = long_decline()
    assert simulate(bars) == simulate(bars, checkpoint=False)


@pytest.mark.parametrize("kind", ["initial", "channel", "risk"])
def test_earlier_safety_exit_is_not_delayed_by_checkpoint(monkeypatch, kind):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    if kind == "initial":
        # Avoid the account's 10% guard taking precedence over the initial stop.
        bars[202] = bars[202].model_copy(update={"close": Decimal(107), "high": Decimal(108)})
        for i in (210, 211):
            bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    elif kind == "channel":
        bars[202] = bars[202].model_copy(update={"close": Decimal(107), "high": Decimal(108)})
        bars[160] = bars[160].model_copy(update={"low": Decimal(99)})
        bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
        bars[210] = bars[210].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    else:
        bars[210] = bars[210].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars)
    assert result.fills[1].time < bars[229].open_time
    assert result.fills[1].reason == ("risk" if kind == "risk" else "signal")


def test_future_price_cannot_move_checkpoint_reservation(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    before = simulate(bars)
    bars[229] = bars[229].model_copy(
        update={"open": Decimal(110), "high": Decimal(115), "close": Decimal(114)}
    )
    after = simulate(bars)
    assert before.fills[1].signal_time == after.fills[1].signal_time == bars[228].close_time
    assert before.fills[1].price != after.fills[1].price


def test_checkpoint_rejects_incompatible_policy_or_manual_fork():
    bars = setup()
    with pytest.raises(ValueError, match="중간 점검"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_exit_checkpoint=True,
        )
    with pytest.raises(ValueError, match="중간 점검"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_exit_checkpoint=True,
            extend_exit_at=bars[204].close_time,
            **MODES,
        )


def test_rejected_checkpoint_sell_retries_without_rechecking_recovery(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    bars[229] = bars[229].model_copy(
        update={"open": Decimal(102), "low": Decimal(101), "close": Decimal(104)}
    )
    result = simulate(bars, capital=Decimal(5100))
    assert result.rejections[0].time == bars[229].open_time
    assert result.rejections[0].side == "sell"
    assert result.fills[1].time == bars[230].open_time
    assert result.fills[1].signal_time == bars[229].close_time


def test_risk_replaces_delayed_checkpoint_order(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    bars[229] = bars[229].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.fills[1].reason == "risk"
    assert result.fills[1].signal_time == bars[229].close_time
    assert result.fills[1].time == bars[231].open_time


def test_next_actual_entry_clears_checkpoint_latch_but_preserves_account(monkeypatch):
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = checkpoint_path()
    bars[229] = bars[229].model_copy(update={"open": Decimal(110), "high": Decimal(111)})
    bars[249] = bars[249].model_copy(update={"close": Decimal(95), "low": Decimal(94)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(115), "high": Decimal(116)})
    for i in range(251, len(bars)):
        bars[i] = bars[i].model_copy(
            update={
                "open": Decimal(115),
                "high": Decimal(116),
                "low": Decimal(114),
                "close": Decimal(115),
            }
        )
    result = simulate(bars)
    assert len(result.fills) == 4
    assert result.fills[2].time == bars[251].open_time
    assert result.fills[3].reason == "settlement"
    assert result.fills[2].price * result.fills[2].quantity + result.fills[2].fee > Decimal(1000000)
    assert result.max_drawdown > 0


def test_evaluator_limits_checkpoint_to_short_context_budget_candidate(tmp_path):
    with pytest.raises(ValueError, match="중간 점검"):
        evaluate(
            {name: [] for name in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            exit_checkpoint_models=("candidate",),
        )


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize(
    (
        "higher_low",
        "extension_floor",
        "liquidation_buffer",
        "liquidation_buffer_long",
        "entry_context",
        "entry_path_context",
    ),
    [
        (False, False, False, False, False, False),
        (True, False, False, False, False, False),
        (False, True, False, False, False, False),
        (False, False, True, False, False, False),
        (False, False, False, True, False, False),
        (False, False, False, False, True, False),
        (False, False, False, False, False, True),
    ],
)
def test_pipeline_checks_full_50_and_51_results(
    tmp_path,
    monkeypatch,
    corrupt,
    higher_low,
    extension_floor,
    liquidation_buffer,
    liquidation_buffer_long,
    entry_context,
    entry_path_context,
):
    bars = checkpoint_path()
    if higher_low:
        bars[199] = bars[199].model_copy(update={"low": Decimal(99)})
    if extension_floor:
        for i in (205, 206):
            bars[i] = bars[i].model_copy(update={"close": Decimal(102), "low": Decimal(101)})
    if liquidation_buffer or liquidation_buffer_long or entry_context or entry_path_context:
        bars[202] = bars[202].model_copy(update={"close": Decimal(108), "high": Decimal(109)})
        bars[203] = bars[203].model_copy(update={"close": Decimal(99), "low": Decimal(98)})
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
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
    write_json(
        ref / "protocol.json",
        {
            "experiment": "59"
            if entry_path_context
            else "58"
            if entry_context
            else "57"
            if liquidation_buffer_long
            else "51"
        },
    )
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    controls = (
        (*runner.MODELS[:2], "liquidation-buffer", "liquidation-buffer-long", "entry-context")
        if entry_path_context
        else (*runner.MODELS[:2], "liquidation-buffer", "liquidation-buffer-long")
        if entry_context
        else (*runner.MODELS[:2], "liquidation-buffer")
        if liquidation_buffer_long
        else runner.MODELS[:2]
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", *controls):
            result = (
                backtest.run_backtest(
                    bars,
                    start,
                    Decimal(1000000),
                    costs(fee, slip),
                    "regime-mlp-v1",
                    extra_delay_bars=delay,
                    regimes={b.close_time: 2 for b in bars if b.close_time >= start},
                    regime_policy="breakout-filter",
                    breakout_liquidation_buffer=True,
                    breakout_entry_context=name == "entry-context",
                    breakout_entry_stop_trend_lookback=168
                    if name == "liquidation-buffer-long"
                    else 24,
                    **MODES,
                )
                if name in ("liquidation-buffer", "liquidation-buffer-long", "entry-context")
                else backtest.run_backtest(
                    bars,
                    start,
                    Decimal(1000000),
                    costs(fee, slip),
                    "breakout-v1",
                    extra_delay_bars=delay,
                )
                if name == "breakout-v1"
                else simulate_control(
                    bars,
                    start,
                    costs(fee, slip),
                    delay=delay,
                    lookback=168 if name == "entry-stop-trend-context" else 24,
                )
            ).model_dump(mode="json")
            if corrupt and name == (
                "entry-context"
                if entry_path_context
                else "liquidation-buffer-long"
                if entry_context
                else "liquidation-buffer"
                if liquidation_buffer_long
                else "entry-stop-trend-context"
            ):
                result["total_fees"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(
                tmp_path,
                ref,
                out,
                higher_low=higher_low,
                extension_floor=extension_floor,
                liquidation_buffer=liquidation_buffer,
                liquidation_buffer_long=liquidation_buffer_long,
                entry_context=entry_context,
                entry_path_context=entry_path_context,
            )
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(
            tmp_path,
            ref,
            out,
            higher_low=higher_low,
            extension_floor=extension_floor,
            liquidation_buffer=liquidation_buffer,
            liquidation_buffer_long=liquidation_buffer_long,
            entry_context=entry_context,
            entry_path_context=entry_path_context,
        )
        status = json.loads((out / "status.json").read_text())
        assert status["baseline_parity"] and status["control_comparisons"] == (
            24
            if entry_path_context
            else 20
            if entry_context
            else 16
            if liquidation_buffer_long
            else 12
        )
        candidate = (
            "entry-path-context"
            if entry_path_context
            else "entry-context"
            if entry_context
            else "liquidation-buffer-long"
            if liquidation_buffer_long
            else "liquidation-buffer"
            if liquidation_buffer
            else "extension-floor-2h"
            if extension_floor
            else "exit-higher-low"
            if higher_low
            else "exit-checkpoint-24h"
        )
        result = json.loads(
            (out / f"seed-17/continuous/block-000/base.{candidate}.json").read_text()
        )
        if higher_low:
            assert result["fills"][1]["reason"] == "settlement"
        else:
            assert result["fills"][1]["signal_time"] == bars[
                203
                if liquidation_buffer
                or liquidation_buffer_long
                or entry_context
                or entry_path_context
                else 206
                if extension_floor
                else 228
            ].close_time.isoformat().replace("+00:00", "Z")
        with pytest.raises(FileExistsError):
            runner.run_study(tmp_path, ref, out)
