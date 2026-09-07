"""Combinations of rule signals and externally supplied model probabilities."""

from decimal import Decimal

from evergreen.strategies.types import Side


def target(signal: Side | None, holding: bool, probability: Decimal) -> Side | None:
    if holding:
        return "sell" if signal == "sell" or probability <= Decimal("0.45") else None
    return "buy" if signal == "buy" and probability >= Decimal("0.60") else None


def meta_target(signal: Side | None, holding: bool, probability: Decimal) -> Side | None:
    if holding:
        return signal
    return "buy" if signal == "buy" and probability >= Decimal(".50") else None
