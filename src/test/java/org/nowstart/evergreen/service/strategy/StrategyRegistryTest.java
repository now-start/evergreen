package org.nowstart.evergreen.service.strategy;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyParams;
import org.nowstart.evergreen.service.strategy.v5.V5StrategyEngine;
import org.nowstart.evergreen.service.strategy.v5.V5StrategyOverrides;

class StrategyRegistryTest {

    @Test
    void requiredWarmupCandles_throwsWhenVersionMissing() {
        StrategyRegistry registry = new StrategyRegistry(List.of(new V5StrategyEngine()));
        registry.init();
        V5StrategyOverrides params = new V5StrategyOverrides(
                2,
                1,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                2,
                BigDecimal.valueOf(0.6),
                BigDecimal.ZERO
        );

        assertThatThrownBy(() -> registry.requiredWarmupCandles("v9", params))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("v9");
    }

    @Test
    void requiredWarmupCandles_throwsForWrongParamType() {
        StrategyRegistry registry = new StrategyRegistry(List.of(new V5StrategyEngine()));
        registry.init();

        StrategyParams wrong = new StrategyParams() {
        };

        assertThatThrownBy(() -> registry.requiredWarmupCandles("v5", wrong))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Invalid params type");
    }

    @Test
    void evaluate_dispatchesToV5Engine() {
        StrategyRegistry registry = new StrategyRegistry(List.of(new V5StrategyEngine()));
        registry.init();

        V5StrategyOverrides params = new V5StrategyOverrides(
                2,
                1,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                2,
                BigDecimal.valueOf(0.6),
                BigDecimal.ZERO
        );
        List<OhlcvCandle> candles = List.of(
                new OhlcvCandle(Instant.parse("2026-01-01T00:00:00Z"), 100, 101, 99, 100, 1000),
                new OhlcvCandle(Instant.parse("2026-01-02T00:00:00Z"), 90, 91, 89, 90, 1000),
                new OhlcvCandle(Instant.parse("2026-01-03T00:00:00Z"), 110, 111, 109, 110, 1000)
        );

        StrategyEvaluation evaluation = registry.evaluate("v5", candles, 2, PositionSnapshot.EMPTY, params);

        org.assertj.core.api.Assertions.assertThat(evaluation.decision().buySignal()).isTrue();
    }

    @Test
    void init_throwsWhenDuplicateEngineVersionRegistered() {
        StrategyRegistry registry = new StrategyRegistry(List.of(new V5StrategyEngine(), new V5StrategyEngine()));

        assertThatThrownBy(registry::init)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("Duplicate strategy engine");
    }

    @Test
    void requiredWarmupCandles_returnsEngineValueForValidParams() {
        StrategyRegistry registry = new StrategyRegistry(List.of(new V5StrategyEngine()));
        registry.init();
        V5StrategyOverrides params = new V5StrategyOverrides(
                120,
                18,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                40,
                BigDecimal.valueOf(0.6),
                BigDecimal.valueOf(0.01)
        );

        int warmup = registry.requiredWarmupCandles("v5", params);

        assertThat(warmup).isEqualTo(120);
    }

    @Test
    void evaluate_throwsWhenStrategyVersionBlank() {
        StrategyRegistry registry = new StrategyRegistry(List.of(new V5StrategyEngine()));
        registry.init();
        V5StrategyOverrides params = new V5StrategyOverrides(
                2,
                1,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                2,
                BigDecimal.valueOf(0.6),
                BigDecimal.ZERO
        );

        assertThatThrownBy(() -> registry.evaluate(" ", List.of(), 0, null, params))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("strategyVersion is required");
    }
}
