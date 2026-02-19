package org.nowstart.evergreen.backtest.model;

public record GridSearchRow(
        StrategyParams params,
        double calmarLike,
        double cagr,
        double mdd,
        double finalEquity
) {}
