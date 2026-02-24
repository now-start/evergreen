package org.nowstart.evergreen.strategy;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.strategy.v5.V5StrategyOverrides;

class StrategyFacadeTest {

    @Test
    void runV5_smokeTestForArrayInput() {
        long[] ts = {
                java.time.Instant.parse("2026-01-01T00:00:00Z").toEpochMilli(),
                java.time.Instant.parse("2026-01-02T00:00:00Z").toEpochMilli(),
                java.time.Instant.parse("2026-01-03T00:00:00Z").toEpochMilli()
        };
        double[] open = {100, 90, 110};
        double[] high = {101, 91, 111};
        double[] low = {99, 89, 109};
        double[] close = {100, 90, 110};
        double[] volume = {1000, 1200, 1300};

        StrategyEvaluation evaluation = StrategyFacade.runV5(
                ts,
                open,
                high,
                low,
                close,
                volume,
                2,
                V5StrategyOverrides.of(120, 18, 2.0, 3.0, 40, 0.6, 0.01)
        );

        assertThat(evaluation).isNotNull();
        assertThat(evaluation.decision()).isNotNull();
    }
}
