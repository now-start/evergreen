package org.nowstart.evergreen.data.dto;

public record TradingSignalVolatilityResult(
        double[] atrPriceRatio,
        double[] percentile,
        boolean[] isHigh
) {
}
