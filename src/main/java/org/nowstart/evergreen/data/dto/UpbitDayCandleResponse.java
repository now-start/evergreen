package org.nowstart.evergreen.data.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.math.BigDecimal;

@JsonIgnoreProperties(ignoreUnknown = true)
public record UpbitDayCandleResponse(
        String candle_date_time_utc,
        BigDecimal opening_price,
        BigDecimal high_price,
        BigDecimal low_price,
        BigDecimal trade_price,
        BigDecimal candle_acc_trade_volume
) {
}
