package org.nowstart.evergreen.backtest.model;

public record StrategyParams(
        double feePerSide,
        double slippage,
        double rsiBuy,
        int maLen,
        int maSlopeDays
) {}
