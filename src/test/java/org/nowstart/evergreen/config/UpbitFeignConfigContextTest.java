package org.nowstart.evergreen.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.cloud.autoconfigure.RefreshAutoConfiguration;
import org.springframework.cloud.context.scope.refresh.RefreshScope;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

class UpbitFeignConfigContextTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withUserConfiguration(RefreshScopeTestConfig.class, UpbitFeignConfig.class, TradingPropsTestConfig.class)
            .withConfiguration(org.springframework.boot.autoconfigure.AutoConfigurations.of(RefreshAutoConfiguration.class));

    @Test
    void contextLoadsWithUpbitFeignConfig() {
        contextRunner.run(context -> {
            assertThat(context).hasNotFailed();
            assertThat(context).hasSingleBean(org.nowstart.evergreen.service.auth.UpbitJwtSigner.class);
        });
    }

    @Configuration(proxyBeanMethods = false)
    static class RefreshScopeTestConfig {

        @Bean
        RefreshScope refreshScope() {
            return new RefreshScope();
        }
    }

    @Configuration(proxyBeanMethods = false)
    static class TradingPropsTestConfig {

        @Bean
        TradingProperties tradingProperties() {
            return new TradingProperties(
                    "https://api.upbit.com",
                    "access",
                    "secret",
                    new BigDecimal("0.0005"),
                    Duration.ofSeconds(30),
                    ExecutionMode.PAPER,
                    List.of("KRW-BTC"),
                    400,
                    true,
                    new BigDecimal("100000"),
                    "v5"
            );
        }
    }
}
