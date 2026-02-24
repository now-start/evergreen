package org.nowstart.evergreen.strategy.v5;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class V5StrategyOverridesTest {

    @Test
    void of_buildsOverrideWithDoubleInputs() {
        V5StrategyOverrides overrides = V5StrategyOverrides.of(120, 18, 2.0, 3.0, 40, 0.6, 0.01);

        assertThat(overrides.regimeEmaLen()).isEqualTo(120);
        assertThat(overrides.atrPeriod()).isEqualTo(18);
        assertThat(overrides.atrMultLowVol()).isEqualByComparingTo("2.0");
        assertThat(overrides.atrMultHighVol()).isEqualByComparingTo("3.0");
        assertThat(overrides.volRegimeLookback()).isEqualTo(40);
        assertThat(overrides.volRegimeThreshold()).isEqualByComparingTo("0.6");
        assertThat(overrides.regimeBand()).isEqualByComparingTo("0.01");
    }

    @Test
    void of_rejectsNonFiniteNumbers() {
        assertThatThrownBy(() -> V5StrategyOverrides.of(
                120,
                18,
                Double.NaN,
                3.0,
                40,
                0.6,
                0.01
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("atrMultLowVol");
    }
}
