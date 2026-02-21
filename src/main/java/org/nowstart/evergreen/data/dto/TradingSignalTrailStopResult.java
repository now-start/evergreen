package org.nowstart.evergreen.data.dto;

public record TradingSignalTrailStopResult(
        double stopPrice,
        boolean triggered
) {
}
