package org.nowstart.evergreen.data.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public record UpbitOrderChanceResponse(
        String bid_fee,
        String ask_fee,
        Account bid_account,
        Account ask_account,
        Market market
) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Account(
            String currency,
            String balance,
            String locked,
            String avg_buy_price
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Market(
            String id,
            String name,
            Object order_types,
            Object order_sides,
            Object bid,
            Object ask,
            MaxTotal max_total
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record MaxTotal(String currency, String max_total) {
    }
}
