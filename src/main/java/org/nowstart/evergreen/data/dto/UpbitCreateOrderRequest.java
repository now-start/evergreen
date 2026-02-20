package org.nowstart.evergreen.data.dto;

public record UpbitCreateOrderRequest(
        String market,
        String side,
        String ord_type,
        String volume,
        String price,
        String identifier
) {
}
