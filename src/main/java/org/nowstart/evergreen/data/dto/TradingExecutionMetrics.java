package org.nowstart.evergreen.data.dto;

public record TradingExecutionMetrics(
        double realizedPnlKrw,
        double realizedReturnPct,
        double maxDrawdownPct,
        int tradeCount,
        double winRatePct,
        double avgWinPct,
        double avgLossPct,
        double rrRatio,
        double expectancyPct
) {
    public static TradingExecutionMetrics empty() {
        return new TradingExecutionMetrics(
                Double.NaN,
                Double.NaN,
                Double.NaN,
                0,
                Double.NaN,
                Double.NaN,
                Double.NaN,
                Double.NaN,
                Double.NaN
        );
    }
}
