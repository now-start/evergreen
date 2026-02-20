package org.nowstart.evergreen.data.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.math.BigDecimal;

@JsonIgnoreProperties(ignoreUnknown = true)
public record UpbitTickerResponse(
        String market,
        BigDecimal trade_price
) {
}
