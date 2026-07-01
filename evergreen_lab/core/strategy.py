from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from evergreen_lab.core.action import Action
from evergreen_lab.core.candle import Candle
from evergreen_lab.core.context import BarContext


class Strategy(ABC):
    """Base class for every strategy. Subclass, implement ``decide``, done.

    Contract (mirrors the live Java engine so migration is 1:1):
    * ``warmup()``   — bars to skip before trading (indicators not yet valid).
    * ``features()`` — optional; precompute causal indicator series ONCE per run.
    * ``reset()``    — optional; clear per-run internal state (e.g. trailing-stop
                        peak). Called by the engine before each backtest so an
                        instance can be reused safely.
    * ``decide(ctx)``— per-bar: return BUY / SELL / HOLD from info up to ``ctx.index``.
    """

    name: ClassVar[str] = ""

    def warmup(self) -> int:
        return 0

    def features(self, candles: Sequence[Candle]) -> dict[str, list[float]]:
        return {}

    def reset(self) -> None:
        return None

    @abstractmethod
    def decide(self, ctx: BarContext) -> Action:
        ...
