package org.nowstart.evergreen.service.strategy;

import jakarta.annotation.PostConstruct;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.service.strategy.core.StrategyParams;
import org.nowstart.evergreen.service.strategy.core.VersionedStrategyParams;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class TradingStrategyParamResolver {

    private final TradingProperties tradingProperties;
    private final List<VersionedStrategyParams> configuredParams;
    private Map<String, VersionedStrategyParams> paramsByVersion = Map.of();

    @PostConstruct
    public void init() {
        if (configuredParams.isEmpty()) {
            throw new IllegalStateException("No strategy params registered");
        }
        Map<String, VersionedStrategyParams> byVersion = new HashMap<>();
        for (VersionedStrategyParams params : configuredParams) {
            String version = normalize(params.version());
            VersionedStrategyParams previous = byVersion.put(version, params);
            if (previous != null) {
                throw new IllegalStateException("Duplicate strategy params registered for version=" + version);
            }
        }
        paramsByVersion = Map.copyOf(byVersion);
    }

    public String resolveActiveStrategyVersion() {
        String raw = tradingProperties.activeStrategyVersion();
        if (raw == null || raw.isBlank()) {
            if (paramsByVersion.size() == 1) {
                return paramsByVersion.keySet().iterator().next();
            }
            throw new IllegalArgumentException("activeStrategyVersion is required");
        }
        return raw.trim().toLowerCase(Locale.ROOT);
    }

    public StrategyParams resolve(String strategyVersion) {
        String normalized = normalize(strategyVersion);
        VersionedStrategyParams params = paramsByVersion.get(normalized);
        if (params == null) {
            throw new IllegalArgumentException("Unsupported strategy version: " + strategyVersion);
        }
        return params;
    }

    public ActiveStrategy resolveActive() {
        String version = resolveActiveStrategyVersion();
        return new ActiveStrategy(version, resolve(version));
    }

    private String normalize(String strategyVersion) {
        if (strategyVersion == null || strategyVersion.isBlank()) {
            throw new IllegalArgumentException("strategyVersion is required");
        }
        return strategyVersion.trim().toLowerCase(Locale.ROOT);
    }

    public record ActiveStrategy(
            String version,
            StrategyParams params
    ) {
    }
}
