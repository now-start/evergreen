package org.nowstart.evergreen.strategy;

import jakarta.annotation.PostConstruct;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.strategy.core.StrategyInput;
import org.nowstart.evergreen.strategy.core.StrategyParams;
import org.nowstart.evergreen.strategy.core.TradingStrategyEngine;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class StrategyRegistry {

    private final List<TradingStrategyEngine<? extends StrategyParams>> engines;
    private Map<String, TradingStrategyEngine<? extends StrategyParams>> enginesByVersion = Map.of();

    @PostConstruct
    void init() {
        Map<String, TradingStrategyEngine<? extends StrategyParams>> byVersion = new HashMap<>();
        for (TradingStrategyEngine<? extends StrategyParams> engine : engines) {
            String version = normalize(engine.version());
            TradingStrategyEngine<? extends StrategyParams> previous = byVersion.put(version, engine);
            if (previous != null) {
                throw new IllegalStateException("Duplicate strategy engine registered for version=" + version);
            }
        }
        enginesByVersion = Map.copyOf(byVersion);
    }

    public TradingStrategyEngine<? extends StrategyParams> getRequired(String strategyVersion) {
        TradingStrategyEngine<? extends StrategyParams> engine = enginesByVersion.get(normalize(strategyVersion));
        if (engine == null) {
            throw new IllegalStateException("No strategy engine registered for version=" + strategyVersion);
        }
        return engine;
    }

    public StrategyEvaluation evaluate(
            String strategyVersion,
            List<OhlcvCandle> candles,
            int signalIndex,
            PositionSnapshot position,
            StrategyParams params
    ) {
        TradingStrategyEngine<? extends StrategyParams> engine = getRequired(strategyVersion);
        return evaluateInternal(engine, candles, signalIndex, position, params);
    }

    public int requiredWarmupCandles(String strategyVersion, StrategyParams params) {
        TradingStrategyEngine<? extends StrategyParams> engine = getRequired(strategyVersion);
        return requiredWarmupInternal(engine, params);
    }

    private <P extends StrategyParams> StrategyEvaluation evaluateInternal(
            TradingStrategyEngine<P> engine,
            List<OhlcvCandle> candles,
            int signalIndex,
            PositionSnapshot position,
            StrategyParams params
    ) {
        P typedParams = castParams(engine, params);
        PositionSnapshot resolvedPosition = position == null ? PositionSnapshot.EMPTY : position;
        return engine.evaluate(new StrategyInput<>(candles, signalIndex, resolvedPosition, typedParams));
    }

    private <P extends StrategyParams> int requiredWarmupInternal(
            TradingStrategyEngine<P> engine,
            StrategyParams params
    ) {
        P typedParams = castParams(engine, params);
        return engine.requiredWarmupCandles(typedParams);
    }

    private <P extends StrategyParams> P castParams(TradingStrategyEngine<P> engine, StrategyParams params) {
        if (!engine.parameterType().isInstance(params)) {
            throw new IllegalArgumentException(
                    "Invalid params type for strategy version=" + engine.version()
                            + ", required=" + engine.parameterType().getSimpleName()
                            + ", actual=" + (params == null ? "null" : params.getClass().getSimpleName())
            );
        }
        return engine.parameterType().cast(params);
    }

    private String normalize(String strategyVersion) {
        if (strategyVersion == null || strategyVersion.isBlank()) {
            throw new IllegalArgumentException("strategyVersion is required");
        }
        return strategyVersion.trim().toLowerCase(Locale.ROOT);
    }
}
