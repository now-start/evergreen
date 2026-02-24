package org.nowstart.evergreen.strategy.v5;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;
import org.nowstart.evergreen.strategy.core.StrategyParams;
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
) implements StrategyParams {

    public V5StrategyOverrides {
        validateFiniteAndMin("atrMultLowVol", atrMultLowVol);
        validateFiniteAndMin("atrMultHighVol", atrMultHighVol);
        validateRange("volRegimeThreshold", volRegimeThreshold, false, true);
        validateRange("regimeBand", regimeBand, true, false);
    }

    public static V5StrategyOverrides of(
            int regimeEmaLen,
            int atrPeriod,
            double atrMultLowVol,
            double atrMultHighVol,
            int volRegimeLookback,
            double volRegimeThreshold,
            double regimeBand
    ) {
        return new V5StrategyOverrides(
                regimeEmaLen,
                atrPeriod,
                toBigDecimal("atrMultLowVol", atrMultLowVol),
                toBigDecimal("atrMultHighVol", atrMultHighVol),
                volRegimeLookback,
                toBigDecimal("volRegimeThreshold", volRegimeThreshold),
                toBigDecimal("regimeBand", regimeBand)
        );
    }

    private static BigDecimal toBigDecimal(String field, double value) {
        if (!Double.isFinite(value)) {
            throw new IllegalArgumentException(field + " must be finite");
        }
        return BigDecimal.valueOf(value);
    }

    private static void validateFiniteAndMin(String field, BigDecimal value) {
        if (value == null) {
            throw new IllegalArgumentException(field + " is required");
        }
        double asDouble = value.doubleValue();
        if (!Double.isFinite(asDouble)) {
            throw new IllegalArgumentException(field + " must be finite");
        }
        boolean valid = asDouble >= 0.0;
        if (!valid) {
            throw new IllegalArgumentException(field + " must be " + (">=") + " " + 0.0);
        }
    }

    private static void validateRange(
            String field,
            BigDecimal value,
            boolean minInclusive,
            boolean maxInclusive
    ) {
        if (value == null) {
            throw new IllegalArgumentException(field + " is required");
        }
        double asDouble = value.doubleValue();
        if (!Double.isFinite(asDouble)) {
            throw new IllegalArgumentException(field + " must be finite");
        }
        boolean minOk = minInclusive ? asDouble >= 0.0 : asDouble > 0.0;
        boolean maxOk = maxInclusive ? asDouble <= 1.0 : asDouble < 1.0;
        if (!minOk || !maxOk) {
            String lower = minInclusive ? "[" : "(";
            String upper = maxInclusive ? "]" : ")";
            throw new IllegalArgumentException(field + " must be in " + lower + 0.0 + ", " + 1.0 + upper);
        }
    }
}
