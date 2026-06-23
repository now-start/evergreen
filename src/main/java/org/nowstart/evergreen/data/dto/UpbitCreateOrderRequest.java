package org.nowstart.evergreen.data.dto;

import com.fasterxml.jackson.annotation.JsonInclude;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record UpbitCreateOrderRequest(
        String market,
        String side,
        String ord_type,
        String volume,
        String price,
        String identifier
) {
}
