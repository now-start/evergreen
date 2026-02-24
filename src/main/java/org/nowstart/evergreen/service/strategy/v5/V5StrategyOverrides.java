package org.nowstart.evergreen.service.strategy.v5;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;
import org.nowstart.evergreen.service.strategy.core.VersionedStrategyParams;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "evergreen.trading.v5")
public record V5StrategyOverrides(
        @Positive int regimeEmaLen,
        @Positive int atrPeriod,
        @NotNull @DecimalMin("0") BigDecimal atrMultLowVol,
        @NotNull @DecimalMin("0") BigDecimal atrMultHighVol,
        @Positive int volRegimeLookback,
        @NotNull @DecimalMin(value = "0", inclusive = false) @DecimalMax("1.0") BigDecimal volRegimeThreshold,
        @NotNull @DecimalMin("0") @DecimalMax("0.999999") BigDecimal regimeBand
) implements VersionedStrategyParams {

    @Override
    public String version() {
        return V5StrategyEngine.VERSION;
    }
}
