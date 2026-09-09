import hashlib
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from evergreen.market import Candle
from evergreen.research.backtest import Costs, run_backtest


def strategy_digest() -> str:
    from evergreen.strategies import PREDICTIVE_STRATEGIES, STRATEGY_PARAMETERS

    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(500):
        price = Decimal(str(round(100 + 20 * math.sin(index / 17) + index / 20, 6)))
        candles.append(
            Candle(
                open_time=start + timedelta(hours=index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal(1),
                quote_volume=price,
                fetched_at=start + timedelta(days=30),
            )
        )
    probabilities = {
        bar.close_time: Decimal((index % 9) / 8) for index, bar in enumerate(candles[199:])
    }
    costs = Costs(
        buy_fee=Decimal(".0005"),
        sell_fee=Decimal(".0005"),
        buy_slippage=Decimal(".001"),
        sell_slippage=Decimal(".001"),
        min_notional=Decimal(5000),
        quantity_step=Decimal(".00000001"),
    )
    digest = hashlib.sha256()
    for name in STRATEGY_PARAMETERS:
        if name in ("regime-rules-v1", "regime-mlp-v1"):
            continue  # New research strategies have separate stateful-regime tests.
        for delay in (0, 1):
            result = run_backtest(
                candles,
                start + timedelta(hours=200),
                Decimal(1000000),
                costs,
                name,
                extra_delay_bars=delay,
                predictions=probabilities if name in PREDICTIVE_STRATEGIES else None,
            )
            digest.update(result.model_dump_json().encode())
    return digest.hexdigest()


def test_all_strategy_results_unchanged_after_packaging() -> None:
    assert strategy_digest() == "8c31e4ad10225ee6daf798f101e39f56fab1bc432ffe6f819ff8758468ab7550"


def test_strategy_package_is_independent_of_execution_and_training() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from evergreen.strategies import strategy_target; "
            'assert "evergreen.research.backtest" not in sys.modules; '
            'assert "torch" not in sys.modules',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_source_identity_covers_nested_strategy_files() -> None:
    from evergreen.research.report import source_identity

    hashes = source_identity()["source_sha256"]
    assert isinstance(hashes, dict)
    assert "strategies/breakout.py" in hashes


def test_signal_modules_handle_short_history_and_require_predictions() -> None:
    from evergreen.strategies import (
        band,
        breakout,
        hybrid,
        rsi,
        signal_target,
        strategy_target,
    )

    for module in (band, breakout, rsi):
        assert module.target([], holding=False) is None
    assert hybrid.meta_target("sell", True, Decimal(".9")) == "sell"
    with pytest.raises(ValueError, match="예측"):
        strategy_target([], False, "mlp-v1")
    with pytest.raises(ValueError, match="예측"):
        signal_target([], False, "meta-mlp-trend-v1")
