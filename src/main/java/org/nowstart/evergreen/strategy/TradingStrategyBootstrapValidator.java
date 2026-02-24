package org.nowstart.evergreen.strategy;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class TradingStrategyBootstrapValidator {

    private final TradingStrategyParamResolver strategyParamResolver;
    private final StrategyRegistry strategyRegistry;

    @PostConstruct
    void validate() {
        String activeVersion = strategyParamResolver.resolveActiveStrategyVersion();
        strategyRegistry.getRequired(activeVersion);
        strategyParamResolver.resolve(activeVersion);
    }
}
