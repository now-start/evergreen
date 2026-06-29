package org.nowstart.evergreen.service.strategy.v6;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;
import org.nowstart.evergreen.service.strategy.core.VersionedStrategyParams;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "evergreen.trading.v6")
public record V6StrategyOverrides(
        @NotNull @DecimalMin(value = "0", inclusive = false) @DefaultValue("0.124825") BigDecimal ruleScale,
        @NotNull @DecimalMin("0") @DefaultValue("0.055") BigDecimal buyCutoff,
        @NotNull @DecimalMin("0") @DefaultValue("0.25") BigDecimal sellCutoff
) implements VersionedStrategyParams {

    @Override
    public String version() {
        return V6StrategyEngine.VERSION;
    }
}
