"""Strategy identifiers and signal types, independent of execution."""

from typing import Literal

Strategy = Literal[
    "cash",
    "buy-hold",
    "sma-trend-v0",
    "sma-slow-v1",
    "breakout-v1",
    "band-reversion-v1",
    "rsi-rebound-v1",
    "mlp-v1",
    "cnn-v1",
    "mlp-trend-v1",
    "cnn-trend-v1",
    "mlp-breakout-v1",
    "cnn-breakout-v1",
    "mlp-rsi-v1",
    "cnn-rsi-v1",
    "trend-12h-v1",
    "breakout-12h-v1",
    "rsi-12h-v1",
    "meta-mlp-trend-v1",
    "meta-cnn-trend-v1",
    "meta-mlp-breakout-v1",
    "meta-cnn-breakout-v1",
    "meta-mlp-rsi-v1",
    "meta-cnn-rsi-v1",
]
Side = Literal["buy", "sell"]
