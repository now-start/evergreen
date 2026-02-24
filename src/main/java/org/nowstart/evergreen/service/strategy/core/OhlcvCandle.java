package org.nowstart.evergreen.service.strategy.core;

import java.time.Instant;

public record OhlcvCandle(
        Instant timestamp,
        double open,
        double high,
        double low,
        double close,
        double volume
) {
}
