from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from evergreen.market import Candle
from evergreen.strategies.buffer import BufferState, Protection, prior_mean_true_range


def bars():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=D(100),
            high=D(101),
            low=D(99),
            close=D(100),
            volume=D(1),
            quote_volume=D(100),
            fetched_at=start + timedelta(days=10),
        )
        for i in range(200)
    ]


def test_prior_range_excludes_current_bar():
    rows = bars()
    rows[-1] = rows[-1].model_copy(update={"high": D(150)})
    assert prior_mean_true_range(rows) == 2


def test_early_activation_and_two_breaches_survive_serialization():
    rows = bars()
    state = BufferState(protection=Protection.open(D(104), D(2)))
    rows[-1] = rows[-1].model_copy(update={"close": D(110), "high": D(111)})
    state.observe(rows, D(0), D(1), D(110), D(0), D(0))
    assert state.protection.threshold == 98
    for i in (1, 2):
        rows.append(
            rows[-1].model_copy(
                update={
                    "open_time": rows[-1].open_time + timedelta(hours=1),
                    "close": D(97),
                    "low": D(96),
                }
            )
        )
        # Ensure the preceding trend is not rising.
        for j in range(len(rows) - 49, len(rows) - 25):
            rows[j] = rows[j].model_copy(update={"close": D(105), "high": D(106)})
        state = BufferState.model_validate_json(state.model_dump_json())
        state.observe(rows, D(0), D(1), D(110), D(0), D(0))
        assert state.protection.breaches == i
    assert state.signal(rows, True) == "sell"


def test_buffer_exit_latches_and_reset_consumes_bar():
    rows = bars()
    state = BufferState(protection=Protection.open(D(100), D(2)))
    state.observe(rows, D(0), D(1), D(130), D(0), D(0))
    assert state.exit_due and state.signal(rows, True) == "sell"
    state.sold(reset=True)
    rows[-1] = rows[-1].model_copy(update={"close": D(98), "low": D(97)})
    assert state.signal(rows, False) is None
    assert not state.awaiting_reset


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_invalid_position_rejected(value):
    with pytest.raises(ValueError):
        Protection.open(D(100), D(value))
