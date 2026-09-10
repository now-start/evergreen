from __future__ import annotations

from decimal import Decimal as D

import pytest
from test_breakout_liquidation_buffer import path

from evergreen.research.buffer_replay import replay
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.buffer_improvement import POLICIES
from evergreen.research.experiments.relaxed_exits import simulate


@pytest.mark.parametrize("delay", [0, 1])
@pytest.mark.parametrize("fee,slip", [(1, 1), (1, 2), (2, 2)])
def test_shared_policy_reproduces_frozen_research_path(delay: int, fee: int, slip: int) -> None:
    bars = path()
    c = costs(fee, slip)
    old = simulate(
        bars, bars[200].open_time, "liquidation-buffer", POLICIES["early3"], c, delay, {}
    )
    actual = replay(bars, bars[200].open_time, D(1000000), c, delay)
    assert actual == old


def test_unsellable_residual_is_included_in_final_equity() -> None:
    bars = path()
    for i in range(201, len(bars)):
        bars[i] = bars[i].model_copy(
            update={"open": D(".10"), "high": D(".11"), "low": D(".09"), "close": D(".10")}
        )
    c = costs()
    actual = replay(bars, bars[200].open_time, D(1000000), c)
    assert actual.btc > 0 and actual.halted
    assert actual.final_equity == actual.cash + actual.btc * bars[-1].close
    assert actual == simulate(
        bars, bars[200].open_time, "liquidation-buffer", POLICIES["early3"], c, 0, {}
    )
