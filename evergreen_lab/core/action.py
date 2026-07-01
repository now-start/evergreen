from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    """A strategy's per-bar decision. Spot only: enter, exit, or do nothing.

    * ``BUY``  — move to fully invested (target ratio 1.0).
    * ``SELL`` — move to flat (target ratio 0.0).
    * ``HOLD`` — keep the current position.

    The engine enforces spot semantics: a ``BUY`` while already fully invested and
    a ``SELL`` while flat both collapse to ``HOLD``.
    """

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
