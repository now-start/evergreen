package org.nowstart.evergreen.strategy;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.strategy.core.StrategyParams;
import org.nowstart.evergreen.strategy.v5.V5StrategyOverrides;

class TradingStrategyParamResolverTest {

    @Test
    void resolveActiveStrategyVersion_normalizesValue() {
        TradingProperties properties = properties(" V5 ");
        V5StrategyOverrides overrides = v5Overrides(120, 18, "2.0", "3.0", 40, "0.6", "0.01");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, overrides);

        assertThat(resolver.resolveActiveStrategyVersion()).isEqualTo("v5");
    }

    @Test
    void resolve_returnsConfiguredV5Overrides() {
        TradingProperties properties = properties("v5");
        V5StrategyOverrides overrides = v5Overrides(200, 14, "1.8", "2.4", 60, "0.7", "0.02");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, overrides);

        StrategyParams params = resolver.resolve("v5");

        assertThat(params).isInstanceOf(V5StrategyOverrides.class);
        V5StrategyOverrides v5 = (V5StrategyOverrides) params;
        assertThat(v5.regimeEmaLen()).isEqualTo(200);
        assertThat(v5.atrPeriod()).isEqualTo(14);
        assertThat(v5.atrMultLowVol()).isEqualByComparingTo("1.8");
        assertThat(v5.atrMultHighVol()).isEqualByComparingTo("2.4");
        assertThat(v5.volRegimeLookback()).isEqualTo(60);
        assertThat(v5.volRegimeThreshold()).isEqualByComparingTo("0.7");
        assertThat(v5.regimeBand()).isEqualByComparingTo("0.02");
    }

    private TradingProperties properties(String activeVersion) {
        return new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                ExecutionMode.PAPER,
                List.of("KRW-BTC"),
                400,
                true,
                new BigDecimal("100000"),
                activeVersion
        );
    }

    private org.nowstart.evergreen.strategy.v5.V5StrategyOverrides v5Overrides(
            int regimeEmaLen,
            int atrPeriod,
            String atrMultLowVol,
            String atrMultHighVol,
            int volRegimeLookback,
            String volRegimeThreshold,
            String regimeBand
    ) {
        return new org.nowstart.evergreen.strategy.v5.V5StrategyOverrides(
                regimeEmaLen,
                atrPeriod,
                new BigDecimal(atrMultLowVol),
                new BigDecimal(atrMultHighVol),
                volRegimeLookback,
                new BigDecimal(volRegimeThreshold),
                new BigDecimal(regimeBand)
        );
    }
}
