package org.nowstart.evergreen.service.strategy.v5;

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
@ConfigurationProperties(prefix = "evergreen.trading.v5")
public record V5StrategyOverrides(
        @Positive @DefaultValue("200") int regimeEmaLen,
        @Positive @DefaultValue("14") int atrPeriod,
        @NotNull @DecimalMin("0") @DefaultValue("2") BigDecimal atrMultLowVol,
        @NotNull @DecimalMin("0") @DefaultValue("4") BigDecimal atrMultHighVol,
        @Positive @DefaultValue("30") int volRegimeLookback,
        @NotNull @DecimalMin(value = "0", inclusive = false) @DecimalMax("1.0") @DefaultValue("0.7") BigDecimal volRegimeThreshold,
        @NotNull @DecimalMin("0") @DecimalMax("0.999999") @DefaultValue("0.02") BigDecimal regimeBand
) implements VersionedStrategyParams {

    @Override
    public String version() {
        return V5StrategyEngine.VERSION;
    }
}
