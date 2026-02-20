package org.nowstart.evergreen.data.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public record UpbitAccountResponse(
        String currency,
        String balance,
        String locked,
        String avg_buy_price,
        String unit_currency
) {
}
