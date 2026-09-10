from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from test_breakout_exit import fixture

from evergreen.research.backtest import run_backtest
from evergreen.research.experiments import breakout_confirmation
from evergreen.research.experiments.breakout_meta import costs


def test_prior_true_range_uses_previous_close_and_excludes_signal() -> None:
    from evergreen.research.breakout_features import prior_mean_true_range

    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[174] = bars[174].model_copy(update={"close": Decimal(90), "low": Decimal(89)})
    assert prior_mean_true_range(bars[:200]) == Decimal("2.375")
    assert prior_mean_true_range(bars[174:200]) == Decimal("2.375")
    bars[199] = bars[199].model_copy(update={"high": Decimal(10000), "low": Decimal(1)})
    assert prior_mean_true_range(bars[:200]) == Decimal("2.375")
    with pytest.raises(ValueError, match="26"):
        prior_mean_true_range(bars[:25])


def test_extension_boundary_prior_ceiling_and_future_invariance() -> None:
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[174] = bars[174].model_copy(update={"close": Decimal(90), "low": Decimal(89)})
    bars[199] = bars[199].model_copy(update={"close": Decimal("103.375")})
    start = bars[200].open_time
    assert breakout_confirmation.extension_schedule(bars, start)[start] == 2
    bars[199] = bars[199].model_copy(update={"close": Decimal("103.376")})
    assert breakout_confirmation.extension_schedule(bars, start)[start] == 0
    bars[199] = bars[199].model_copy(update={"high": Decimal(10000), "low": Decimal(1)})
    assert breakout_confirmation.extension_schedule(bars, start)[start] == 0
    bars[200] = bars[200].model_copy(update={"high": Decimal(20000)})
    assert breakout_confirmation.extension_schedule(bars, start)[start] == 0
    assert breakout_confirmation.extension_schedule(
        bars[:168], bars[0].close_time
    ) == dict.fromkeys((b.close_time for b in bars[:168]), 0)


@pytest.mark.parametrize("delay", [0, 1])
def test_entry_cap_preserves_fills_original_exit_and_risk(delay: int) -> None:
    bars = fixture()
    # Failure exits would act at210; original48 floor still includes the low at180.
    bars[210] = bars[210].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    bars[250] = bars[250].model_copy(update={"close": Decimal(98), "low": Decimal(97)})
    start = bars[200].open_time
    args = (bars, start, Decimal(1000000), costs())
    original = run_backtest(*args, "breakout-v1", extra_delay_bars=delay)
    result = run_backtest(
        *args,
        "regime-mlp-v1",
        regimes=breakout_confirmation.extension_schedule(bars, start),
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
    )
    assert result.fills == original.fills
    assert result.fills[0].time == bars[200 + delay].open_time
    assert result.fills[1].time == bars[251 + delay].open_time
    assert result.fills[1].reason == "signal"
    # A low approval while holding must not suppress risk handling either.
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    scores = breakout_confirmation.extension_schedule(bars, start)
    scores[bars[250].close_time] = 0
    result = run_backtest(
        *args,
        "regime-mlp-v1",
        regimes=scores,
        regime_policy="breakout-filter",
        extra_delay_bars=delay,
    )
    assert result.halted and result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[251 + delay].open_time


def test_zero_range_and_approval_cannot_manufacture_entry() -> None:
    bars = [
        b.model_copy(update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100)))
        for b in fixture()
    ]
    start = bars[200].open_time
    assert breakout_confirmation.extension_schedule(bars, start)[start] == 2
    for breakout in (False, True):
        if breakout:
            bars[199] = bars[199].model_copy(update={"high": Decimal(102), "close": Decimal(101)})
            assert breakout_confirmation.extension_schedule(bars, start)[start] == 0
        result = run_backtest(
            bars,
            start,
            Decimal(1000000),
            costs(),
            "regime-mlp-v1",
            regimes=breakout_confirmation.extension_schedule(bars, start),
            regime_policy="breakout-filter",
        )
        assert not result.fills


@pytest.mark.parametrize("other", ["risk_budget", "volatility_failure_exit"])
def test_entry_cap_cannot_combine_with_other_research_options(tmp_path: Path, other: str) -> None:
    with pytest.raises(ValueError, match="동시"):
        breakout_confirmation.run_study(
            tmp_path, tmp_path, tmp_path / "out", extension_cap=True, **{other: True}
        )
    assert not (tmp_path / "out").exists()
