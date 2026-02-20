package org.nowstart.evergreen.data.dto;

import java.math.BigDecimal;

public record BalanceDto(
        String currency,
        BigDecimal balance,
        BigDecimal locked,
        BigDecimal avgBuyPrice,
        String unitCurrency
) {
}
