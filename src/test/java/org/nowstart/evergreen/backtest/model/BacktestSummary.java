package org.nowstart.evergreen.backtest.model;

public record BacktestSummary(
        double finalEquity,
        double finalEquityBh,
        double cagr,
        double mdd,
        int trades,
        String range
) {}
