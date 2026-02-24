package org.nowstart.evergreen.strategy.core;

import org.nowstart.evergreen.data.dto.TradingSignalQualityStats;
import org.nowstart.evergreen.data.type.MarketRegime;

public record StrategyEvaluation(
        StrategySignalDecision decision,
        MarketRegime previousRegime,
        MarketRegime currentRegime,
        double regimeAnchor,
        double regimeUpper,
        double regimeLower,
        double atr,
        double atrMultiplier,
        double atrTrailStop,
        boolean trailStopTriggered,
        boolean volatilityHigh,
        double atrPriceRatio,
        double volPercentile,
        TradingSignalQualityStats signalQuality
) {
}
