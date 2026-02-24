package org.nowstart.evergreen.strategy;

import java.util.Locale;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.strategy.core.StrategyParams;
import org.nowstart.evergreen.strategy.v5.V5StrategyEngine;
import org.nowstart.evergreen.strategy.v5.V5StrategyOverrides;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class TradingStrategyParamResolver {

    private final TradingProperties tradingProperties;
    private final V5StrategyOverrides v5StrategyOverrides;

    public String resolveActiveStrategyVersion() {
        String raw = tradingProperties.activeStrategyVersion();
        if (raw == null || raw.isBlank()) {
            return V5StrategyEngine.VERSION;
        }
        return raw.trim().toLowerCase(Locale.ROOT);
    }

    public StrategyParams resolve(String strategyVersion) {
        String normalized = normalize(strategyVersion);
        return switch (normalized) {
            case V5StrategyEngine.VERSION -> v5StrategyOverrides;
            default -> throw new IllegalArgumentException("Unsupported strategy version: " + strategyVersion);
        };
    }

    private String normalize(String strategyVersion) {
        if (strategyVersion == null || strategyVersion.isBlank()) {
            throw new IllegalArgumentException("strategyVersion is required");
        }
        return strategyVersion.trim().toLowerCase(Locale.ROOT);
    }
}
