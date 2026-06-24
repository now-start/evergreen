package org.nowstart.evergreen.service.strategy.v1;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.PositiveOrZero;
import java.math.BigDecimal;
import org.nowstart.evergreen.service.strategy.core.VersionedStrategyParams;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "evergreen.trading.v1")
public record V1StrategyOverrides(
        @DecimalMin("0") @DecimalMax("100") @DefaultValue("30") BigDecimal rsiBuy,
        @Positive @DefaultValue("60") int maLen,
        @PositiveOrZero @DefaultValue("3") int maSlopeDays
) implements VersionedStrategyParams {

    @Override
    public String version() {
        return V1StrategyEngine.VERSION;
    }
}
