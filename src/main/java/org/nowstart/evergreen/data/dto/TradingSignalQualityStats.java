package org.nowstart.evergreen.data.dto;

public record TradingSignalQualityStats(
        double avg1dPct,
        double avg3dPct,
        double avg7dPct
) {
}
