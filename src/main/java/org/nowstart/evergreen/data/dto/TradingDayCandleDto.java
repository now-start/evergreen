package org.nowstart.evergreen.data.dto;

import java.math.BigDecimal;
import java.time.Instant;

public record TradingDayCandleDto(
        Instant timestamp,
        BigDecimal high,
        BigDecimal low,
        BigDecimal close
) {
}
