package org.nowstart.evergreen.data.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record UpbitOrderResponse(
        String uuid,
        String side,
        String ord_type,
        String price,
        String state,
        String market,
        String created_at,
        String volume,
        String remaining_volume,
        String reserved_fee,
        String remaining_fee,
        String paid_fee,
        String locked,
        String executed_volume,
        int trades_count,
        List<UpbitTrade> trades
) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record UpbitTrade(
            String market,
            String uuid,
            String price,
            String volume,
            String funds,
            String side,
            String created_at
    ) {
    }
}
