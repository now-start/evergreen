package org.nowstart.evergreen.service.strategy.v4;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;
import org.nowstart.evergreen.service.strategy.core.VersionedStrategyParams;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "evergreen.trading.v4")
public record V4StrategyOverrides(
        @Positive @DefaultValue("200") int regimeEmaLen,
        @Positive @DefaultValue("14") int atrPeriod,
        @NotNull @DecimalMin("0") @DefaultValue("3") BigDecimal atrTrailMultiplier,
        @NotNull @DecimalMin("0") @DecimalMax("0.999999") @DefaultValue("0.02") BigDecimal regimeBand,
        @Positive @DefaultValue("20") int weeklyEmaLen
) implements VersionedStrategyParams {

    @Override
    public String version() {
        return V4StrategyEngine.VERSION;
    }
}
