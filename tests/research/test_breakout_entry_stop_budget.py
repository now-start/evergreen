from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import pytest
from test_breakout_entry_stop import setup

from evergreen.market import Candle, write_json
from evergreen.research import backtest
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments import breakout_confirmation as runner
from evergreen.research.experiments.breakout_meta import costs, evaluate
from evergreen.research.experiments.regime import SCENARIOS


def simulate(
    bars: list[Candle],
    *,
    enabled: bool = True,
    delay: int = 0,
    capital: Decimal | int = 1000000,
    charges: Costs | None = None,
) -> Result:
    return backtest.run_backtest(
        bars,
        bars[200].open_time,
        Decimal(capital),
        charges or costs(),
        "breakout-v1",
        breakout_entry_stop=True,
        breakout_entry_stop_confirmations=2,
        breakout_entry_stop_channel_reset=True,
        breakout_entry_stop_profit_trail=True,
        breakout_entry_stop_adaptive_trail=True,
        breakout_entry_stop_trend_confirmation=True,
        breakout_entry_stop_budget=enabled,
        extra_delay_bars=delay,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_default_affordable_entry_preserves_control_and_frozen_signal_tr(delay: int) -> None:
    bars = setup()
    # Low=1 in the signal bar would reject if incorrectly used in the entry TR.
    assert simulate(bars, delay=delay) == simulate(bars, enabled=False, delay=delay)
    assert simulate(bars, delay=delay).fills[0].time == bars[200 + delay].open_time


@pytest.mark.parametrize("delay", [0, 1])
def test_checks_actual_open_not_signal_close_or_future_range(
    monkeypatch: pytest.MonkeyPatch, delay: int
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal("3.5"))
    bars = setup()
    assert simulate(bars, delay=delay).rejections[0].reason == "risk_budget"
    i = 200 + delay
    bars[i] = bars[i].model_copy(update={"open": Decimal(120), "high": Decimal(121)})
    result = simulate(bars, delay=delay)
    assert result.fills[0].time == bars[i].open_time
    assert result.fills[0].reference_price == 120
    bars[i] = bars[i].model_copy(update={"high": Decimal(500), "low": Decimal(1)})
    assert simulate(bars, delay=delay).fills[0] == result.fills[0]


def test_costs_change_budget_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal("3.4"))
    bars = setup()
    assert simulate(bars).rejections[0].reason == "risk_budget"
    free = costs(0, 0)
    assert simulate(bars, charges=free).fills[0].time == bars[200].open_time


def test_projected_liquidation_includes_rounding_cash_dust_and_old_peak() -> None:
    c = costs().model_copy(update={"quantity_step": Decimal(".7")})
    portfolio = backtest._Portfolio(Decimal(10000), c, True)
    portfolio.cash = Decimal(9500)
    reference, floor = Decimal(104), Decimal(100)
    price = reference * (1 + c.buy_slippage)
    q = (portfolio.cash / (1 + c.buy_fee) / price / c.quantity_step).to_integral_value(
        rounding=ROUND_DOWN
    ) * c.quantity_step
    projected = (
        portfolio.cash
        - q * price * (1 + c.buy_fee)
        + q * floor * (1 - c.sell_slippage) * (1 - c.sell_fee)
    )
    portfolio.peak = (projected / Decimal(".9")).quantize(Decimal(".0000001"), rounding=ROUND_DOWN)
    assert portfolio.channel_entry_fits(reference, floor)
    portfolio.peak += Decimal(".0001")
    assert not portfolio.channel_entry_fits(reference, floor)
    assert portfolio.cash == 9500 and portfolio.btc == 0 and not portfolio.fills
    portfolio.peak = Decimal(10000)
    assert not portfolio.channel_entry_fits(Decimal(100), Decimal(94))
    # A finite exact equality must also pass (no recurring Decimal division).
    equal = backtest._Portfolio(Decimal(10000), costs(0, 0), True)
    assert equal.channel_entry_fits(Decimal(100), Decimal(90))
    assert not equal.channel_entry_fits(Decimal(100), Decimal("89.9999"))


def test_losing_trade_does_not_reset_account_peak_for_next_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(2))
    bars = setup()
    for i in (202, 203):
        bars[i] = bars[i].model_copy(update={"close": Decimal(97), "low": Decimal(96)})
    bars[204] = bars[204].model_copy(update={"open": Decimal(99), "low": Decimal(98)})
    bars[249] = bars[249].model_copy(update={"close": Decimal(95), "low": Decimal(94)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(110), "high": Decimal(111)})
    control, result = simulate(bars, enabled=False), simulate(bars)
    assert result.fills == control.fills[:2]
    assert control.fills[2].time == bars[251].open_time
    assert result.rejections[-1].time == bars[251].open_time
    assert result.rejections[-1].reason == "risk_budget"
    assert result.cash == result.fills[1].cash_after and not result.halted


@pytest.mark.parametrize("tr", [Decimal(0), Decimal(40)])
def test_undefined_or_nonpositive_stop_rejects_without_mutating_account(
    monkeypatch: pytest.MonkeyPatch, tr: Decimal
) -> None:
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: tr)
    result = simulate(setup())
    assert not result.fills and result.cash == 1000000 and result.btc == 0
    assert not result.halted and result.max_drawdown == 0
    assert result.rejections and all(r.reason == "risk_budget" for r in result.rejections)


def test_rejected_order_can_retry_without_phantom_position(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = setup()
    # First original signal TR rejects. A later fresh signal has affordable TR.
    monkeypatch.setattr(
        backtest,
        "prior_mean_true_range",
        lambda h: Decimal(4 if h[-1].open_time == bars[199].open_time else 2),
    )
    bars[200] = bars[200].model_copy(update={"close": Decimal(106), "high": Decimal(107)})
    result = simulate(bars)
    assert result.rejections[0].time == bars[200].open_time
    assert result.rejections[0].reason == "risk_budget"
    assert result.fills[0].time == bars[201].open_time
    assert result.fills[0].signal_time == bars[200].close_time
    assert result.fills[0].side == "buy" and result.fills[1].side == "sell"
    assert result.fills[0].quantity == simulate(bars, enabled=False).fills[0].quantity


def test_delayed_budget_uses_original_tr_and_risk_sell_is_not_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = setup()
    monkeypatch.setattr(
        backtest,
        "prior_mean_true_range",
        lambda h: Decimal(2 if h[-1].open_time == bars[199].open_time else 40),
    )
    bars[202] = bars[202].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = simulate(bars, delay=1)
    assert result.fills[0].time == bars[201].open_time
    assert result.halted and result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[204].open_time
    assert not result.rejections


def test_budget_does_not_hide_minimum_order_rejection() -> None:
    result = simulate(setup(), capital=1000)
    assert not result.fills and result.rejections[0].reason == "below_minimum"


def test_budget_requires_declared_trend_candidate(tmp_path: Path) -> None:
    bars = setup()
    with pytest.raises(ValueError, match="손절 예산"):
        backtest.run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_entry_stop_budget=True,
        )
    with pytest.raises(ValueError, match="손절 예산"):
        evaluate(
            {n: [] for n in ("breakout-v1", "cash", "prior", "candidate")},
            tmp_path,
            models=("candidate",),
            entry_stop_budget_models=("candidate",),
        )
    with pytest.raises(ValueError, match="동시"):
        runner.run_study(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            entry_stop_budget=True,
            entry_stop_trend_confirmation=True,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_pipeline_preserves_experiment49_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    bars = setup()
    monkeypatch.setattr(backtest, "prior_mean_true_range", lambda h: Decimal(4))
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
    write_json(ref / "protocol.json", {"experiment": "49"})
    write_json(ref / "status.json", {"status": "completed_partial_coverage"})
    write_json(
        ref / "intervals.json",
        [{"start": start.isoformat(), "end": bars[-1].close_time.isoformat()}],
    )
    for scenario, fee, slip, delay in SCENARIOS:
        for name in ("breakout-v1", "entry-stop-trend-confirmation"):
            filtered = name != "breakout-v1"
            result = backtest.run_backtest(
                bars,
                start,
                Decimal(1000000),
                costs(fee, slip),
                "regime-mlp-v1" if filtered else "breakout-v1",
                extra_delay_bars=delay,
                regimes={b.close_time: 2 for b in bars[199:]} if filtered else None,
                regime_policy="breakout-filter" if filtered else "routing",
                breakout_entry_stop=filtered,
                breakout_entry_stop_confirmations=2 if filtered else 1,
                breakout_entry_stop_channel_reset=filtered,
                breakout_entry_stop_profit_trail=filtered,
                breakout_entry_stop_adaptive_trail=filtered,
                breakout_entry_stop_trend_confirmation=filtered,
            ).model_dump(mode="json")
            if corrupt and filtered:
                result["net_return"] = "123"
            write_json(folder / f"{scenario}.{name}.json", result)
    out = tmp_path / "out"
    if corrupt:
        with pytest.raises(ValueError, match="재현"):
            runner.run_study(tmp_path, ref, out, entry_stop_budget=True)
        assert (out / "failure.json").exists() and not (out / "status.json").exists()
    else:
        runner.run_study(tmp_path, ref, out, entry_stop_budget=True)
        result = json.loads(
            (out / "seed-17/continuous/block-000/base.entry-stop-budget.json").read_text()
        )
        assert not result["fills"] and result["rejections"][0]["reason"] == "risk_budget"
        assert json.loads((out / "protocol.json").read_text())["experiment"] == "50"
        assert json.loads((out / "status.json").read_text())["baseline_parity"]
