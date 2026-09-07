"""Probability-based signals; neural-network training remains in learning.py."""

from decimal import Decimal

from evergreen.strategies.types import Side


def target(holding: bool, probability: Decimal) -> Side | None:
    if holding:
        return "sell" if probability <= Decimal("0.45") else None
    return "buy" if probability >= Decimal("0.60") else None
