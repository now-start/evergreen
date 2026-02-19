package org.nowstart.evergreen.backtest.model;

public record StrategyParams(
        double feePerSide,
        double slippage,
        int regimeEmaLen,
        int atrPeriod,
        double atrTrailMultiplier,
        double regimeBand
) {}
