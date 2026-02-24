package org.nowstart.evergreen.strategy;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.service.strategy.TradingStrategyParamResolver;
import org.nowstart.evergreen.service.strategy.core.StrategyParams;
import org.nowstart.evergreen.service.strategy.v5.V5StrategyOverrides;

class TradingStrategyParamResolverTest {

    @Test
    void resolveActiveStrategyVersion_normalizesValue() {
        TradingProperties properties = properties(" V5 ");
        V5StrategyOverrides overrides = v5Overrides(120, 18, "2.0", "3.0", 40, "0.6", "0.01");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, List.of(overrides));
        resolver.init();

        assertThat(resolver.resolveActiveStrategyVersion()).isEqualTo("v5");
    }

    @Test
    void resolve_returnsConfiguredV5Overrides() {
        TradingProperties properties = properties("v5");
        V5StrategyOverrides overrides = v5Overrides(200, 14, "1.8", "2.4", 60, "0.7", "0.02");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, List.of(overrides));
        resolver.init();

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

    @Test
    void resolveActive_returnsVersionAndParamsTogether() {
        TradingProperties properties = properties(" v5 ");
        V5StrategyOverrides overrides = v5Overrides(120, 18, "2.0", "3.0", 40, "0.6", "0.01");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, List.of(overrides));
        resolver.init();

        TradingStrategyParamResolver.ActiveStrategy active = resolver.resolveActive();

        assertThat(active.version()).isEqualTo("v5");
        assertThat(active.params()).isSameAs(overrides);
    }

    @Test
    void resolve_throwsWhenVersionUnsupported() {
        TradingProperties properties = properties("v9");
        V5StrategyOverrides overrides = v5Overrides(120, 18, "2.0", "3.0", 40, "0.6", "0.01");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, List.of(overrides));
        resolver.init();

        assertThatThrownBy(() -> resolver.resolve("v9"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("v9");
    }

    @Test
    void init_throwsWhenDuplicateVersionConfigured() {
        TradingProperties properties = properties("v5");
        V5StrategyOverrides first = v5Overrides(120, 18, "2.0", "3.0", 40, "0.6", "0.01");
        V5StrategyOverrides second = v5Overrides(200, 20, "2.1", "3.1", 50, "0.7", "0.02");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, List.of(first, second));

        assertThatThrownBy(resolver::init)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("v5");
    }

    @Test
    void init_throwsWhenNoStrategyParamsRegistered() {
        TradingProperties properties = properties("v5");
        TradingStrategyParamResolver resolver = new TradingStrategyParamResolver(properties, List.of());

        assertThatThrownBy(resolver::init)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("No strategy params");
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

    private V5StrategyOverrides v5Overrides(
            int regimeEmaLen,
            int atrPeriod,
            String atrMultLowVol,
            String atrMultHighVol,
            int volRegimeLookback,
            String volRegimeThreshold,
            String regimeBand
    ) {
        return new V5StrategyOverrides(
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
