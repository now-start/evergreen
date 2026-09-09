from decimal import Decimal

import pytest
from test_breakout_exit import fixture

from evergreen.research.backtest import run_backtest
from evergreen.research.experiments.breakout_meta import costs


def test_buffer_uses_prior_true_range_and_is_frozen_with_delayed_fills():
    bars = fixture()
    bars[180] = bars[180].model_copy(update={"low": Decimal(99)})
    bars[174] = bars[174].model_copy(update={"close": Decimal(90), "low": Decimal(89)})
    # Prior24 starts at175: TR175=11 via previous close90; other23 ranges=2.
    # Frozen threshold101 -57/24=98.625. Signal and post-signal ranges excluded.
    bars[199] = bars[199].model_copy(update={"high": Decimal(200), "low": Decimal(1)})
    bars[200] = bars[200].model_copy(update={"high": Decimal(300)})
    bars[205] = bars[205].model_copy(update={"close": Decimal("98.625"), "low": Decimal(98)})
    bars[210] = bars[210].model_copy(update={"close": Decimal("98.5"), "low": Decimal(98)})
    bars[211] = bars[211].model_copy(update={"open": Decimal("98.5"), "low": Decimal(98)})
    for delay in (0, 1):
        result = run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_failure_exit=True,
            breakout_failure_buffer=True,
            extra_delay_bars=delay,
        )
        assert result.fills[0].time == bars[200 + delay].open_time
        assert result.fills[1].time == bars[211 + delay].open_time
        assert result.fills[1].reference_price == bars[211 + delay].open
        assert result.fills[1].reason == "signal"
    before = result.fills[:2]
    bars[-1] = bars[-1].model_copy(update={"high": Decimal(10000)})
    assert (
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_failure_exit=True,
            breakout_failure_buffer=True,
            extra_delay_bars=1,
        ).fills[:2]
        == before
    )
    # A second buy must get a new frozen level and a new prior24 range estimate.
    bars[240] = bars[240].model_copy(update={"close": Decimal(304), "high": Decimal(305)})
    bars[241] = bars[241].model_copy(
        update={
            "open": Decimal(304),
            "close": Decimal(303),
            "high": Decimal(306),
            "low": Decimal(302),
        }
    )
    bars[242] = bars[242].model_copy(
        update={
            "open": Decimal(303),
            "close": Decimal(298),
            "high": Decimal(306),
            "low": Decimal(297),
        }
    )
    bars[243] = bars[243].model_copy(
        update={
            "open": Decimal(298),
            "close": Decimal(297),
            "high": Decimal(299),
            "low": Decimal(296),
        }
    )
    bars[244] = bars[244].model_copy(
        update={
            "open": Decimal(297),
            "close": Decimal(297),
            "high": Decimal(298),
            "low": Decimal(296),
        }
    )
    result = run_backtest(
        bars,
        bars[200].open_time,
        Decimal(1000000),
        costs(),
        "breakout-v1",
        breakout_failure_exit=True,
        breakout_failure_buffer=True,
    )
    assert [(fill.side, fill.time) for fill in result.fills[:4]] == [
        ("buy", bars[200].open_time),
        ("sell", bars[211].open_time),
        ("buy", bars[241].open_time),
        ("sell", bars[244].open_time),
    ]


def test_zero_range_matches_unbuffered_exit():
    bars = fixture()
    for i in range(199):
        bars[i] = bars[i].model_copy(
            update=dict.fromkeys(("open", "high", "low", "close"), Decimal(100))
        )
    bars[210] = bars[210].model_copy(update={"close": Decimal("99.9"), "low": Decimal(99)})
    args = (bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1")
    original = run_backtest(*args, breakout_failure_exit=True)
    assert original == run_backtest(*args, breakout_failure_exit=True, breakout_failure_buffer=True)


@pytest.mark.parametrize("delay", [0, 1])
def test_buffer_does_not_suppress_channel_exit_or_risk(delay):
    bars = fixture()
    bars[250] = bars[250].model_copy(update={"close": Decimal(100), "low": Decimal(99)})
    options = {
        "breakout_failure_exit": True,
        "breakout_failure_buffer": True,
        "extra_delay_bars": delay,
    }
    result = run_backtest(
        bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1", **options
    )
    assert result.fills[1].time == bars[251 + delay].open_time
    assert result.fills[1].reason == "signal"
    bars[250] = bars[250].model_copy(update={"close": Decimal(70), "low": Decimal(69)})
    result = run_backtest(
        bars, bars[200].open_time, Decimal(1000000), costs(), "breakout-v1", **options
    )
    assert result.halted and result.fills[1].reason == "risk"
    assert result.fills[1].time == bars[251 + delay].open_time


@pytest.mark.parametrize("enabled, confirmations", [(False, 1), (True, 2)])
def test_buffer_rejects_disabled_or_combined_modes(enabled, confirmations):
    bars = fixture()
    with pytest.raises(ValueError, match="변동 폭"):
        run_backtest(
            bars,
            bars[200].open_time,
            Decimal(1000000),
            costs(),
            "breakout-v1",
            breakout_failure_exit=enabled,
            breakout_failure_confirmations=confirmations,
            breakout_failure_buffer=True,
        )
