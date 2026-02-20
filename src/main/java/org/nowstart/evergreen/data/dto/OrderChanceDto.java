package org.nowstart.evergreen.data.dto;

import java.math.BigDecimal;

public record OrderChanceDto(
        String market,
        BigDecimal bidFee,
        BigDecimal askFee,
        BigDecimal bidBalance,
        BigDecimal askBalance,
        BigDecimal maxTotal
) {
}
