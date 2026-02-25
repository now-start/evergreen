package org.nowstart.evergreen.service.strategy.v5;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class V5StrategyOverridesTest {

    @Test
    void constructor_buildsOverride() {
        V5StrategyOverrides overrides = new V5StrategyOverrides(
                120,
                18,
                BigDecimal.valueOf(2.0),
                BigDecimal.valueOf(3.0),
                40,
                BigDecimal.valueOf(0.6),
                BigDecimal.valueOf(0.01)
        );

        assertThat(overrides.regimeEmaLen()).isEqualTo(120);
        assertThat(overrides.atrPeriod()).isEqualTo(18);
        assertThat(overrides.atrMultLowVol()).isEqualByComparingTo("2.0");
        assertThat(overrides.atrMultHighVol()).isEqualByComparingTo("3.0");
        assertThat(overrides.volRegimeLookback()).isEqualTo(40);
        assertThat(overrides.volRegimeThreshold()).isEqualByComparingTo("0.6");
        assertThat(overrides.regimeBand()).isEqualByComparingTo("0.01");
    }
}
